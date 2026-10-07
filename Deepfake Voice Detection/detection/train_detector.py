"""A small real-vs-fake voice detector. No GPU, no torch, no download but the data.

Layout your audio like this - two folders, that's the whole labelling scheme:

    corpus/real/*.wav      genuine human speech
    corpus/fake/*.wav      synthesised / cloned speech

Then:

    python train_detector.py corpus              # train + test, prints EER
    python train_detector.py corpus --save m.pkl # keep the model
    python train_detector.py --predict m.pkl clip.wav

Speakers are NEVER split across train and test: a model that has heard a
speaker's real voice can recognise them rather than the spoofing, and the score
comes out great and means nothing. Filenames are assumed to start with a
speaker id (id01234-xyz.wav, LA_0079_...). Override with --speaker-regex.
"""

import argparse
import re
import sys
import time
from pathlib import Path

import joblib
import librosa
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_curve

SAMPLE_RATE = 16000
MAX_SECONDS = 4.0          # clip length fed to the model; keeps timing even
AUDIO_EXT = {".wav", ".flac", ".m4a", ".mp3", ".ogg", ".opus"}


def _load_audio(path):
    """librosa first, pyav on decoder failure.

    libsndfile 1.2.2 throws "unknown error in flac decoder" on a good share of
    ASVspoof 2021 DF -- the header reads fine, the audio does not. librosa then
    falls back to audioread, which has no backend here, so those clips were
    being dropped silently (4 of 6 in a sample). pyav decodes them, and agrees
    with soundfile bit-for-bit on the files both can read.
    """
    try:
        return librosa.load(path, sr=SAMPLE_RATE, mono=True,
                            duration=MAX_SECONDS)
    except Exception:
        import av

        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            resampler = av.audio.resampler.AudioResampler(
                format="flt", layout="mono", rate=SAMPLE_RATE)
            chunks, want = [], int(MAX_SECONDS * SAMPLE_RATE)
            total = 0
            for frame in container.decode(stream):
                for resampled in resampler.resample(frame):
                    chunk = resampled.to_ndarray().ravel()
                    chunks.append(chunk)
                    total += len(chunk)
                if total >= want:
                    break
        if not chunks:
            raise
        return np.concatenate(chunks)[:want], SAMPLE_RATE


def features(path):
    """~90 numbers per clip. MFCC mean+std is the workhorse; the spectral
    stats catch the vocoder ceiling that cloned speech tends to leave."""
    audio, sr = _load_audio(path)
    if audio.size < sr // 4:
        raise ValueError("clip shorter than 0.25s")

    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20)
    delta = librosa.feature.delta(mfcc)
    stft = np.abs(librosa.stft(audio, n_fft=1024, hop_length=256))

    return np.concatenate([
        mfcc.mean(axis=1), mfcc.std(axis=1),
        delta.mean(axis=1), delta.std(axis=1),
        [
            librosa.feature.spectral_centroid(S=stft, sr=sr).mean(),
            librosa.feature.spectral_rolloff(S=stft, sr=sr, roll_percent=0.95).mean(),
            librosa.feature.spectral_flatness(S=stft).mean(),
            librosa.feature.spectral_bandwidth(S=stft, sr=sr).mean(),
            librosa.feature.zero_crossing_rate(audio).mean(),
        ],
    ]).astype(np.float32)


def speaker_of(path, pattern):
    m = re.match(pattern, path.stem)
    # no match -> the file is its own speaker, which is the safe direction:
    # it can never leak across the split
    return m.group(1) if m else path.stem


def load(corpus, pattern, limit):
    """-> X, y, speakers.  y: 1 = fake, 0 = real."""
    X, y, spk = [], [], []
    for label, folder in ((0, "real"), (1, "fake")):
        d = Path(corpus) / folder
        if not d.is_dir():
            sys.exit("Missing folder: %s\nExpected %s/real/ and %s/fake/"
                     % (d, corpus, corpus))
        files = sorted(p for p in d.rglob("*") if p.suffix.lower() in AUDIO_EXT)
        if limit:
            files = files[:limit]
        if not files:
            sys.exit("No audio in %s" % d)
        print("  %s: %d clips" % (folder, len(files)), file=sys.stderr)
        for i, p in enumerate(files, 1):
            try:
                X.append(features(str(p)))
            except Exception:
                continue
            y.append(label)
            spk.append(speaker_of(p, pattern))
            if i % 200 == 0:
                print("    %d/%d" % (i, len(files)), file=sys.stderr)
    return np.array(X), np.array(y), np.array(spk)


def split_by_speaker(spk, test_frac, seed=0):
    """Whole speakers go to one side or the other. Never split a speaker."""
    uniq = np.unique(spk)
    rng = np.random.default_rng(seed)
    rng.shuffle(uniq)
    n_test = max(1, int(len(uniq) * test_frac))
    test_spk = set(uniq[:n_test].tolist())
    is_test = np.array([s in test_spk for s in spk])
    return ~is_test, is_test


def eer(y_true, scores):
    """Equal error rate: the point where false accepts == false rejects.
    One number, lower is better, and the number ASVspoof papers report."""
    fpr, tpr, thr = roc_curve(y_true, scores)
    fnr = 1 - tpr
    i = np.nanargmin(np.abs(fnr - fpr))
    return float((fpr[i] + fnr[i]) / 2), float(thr[i])


def train(args):
    print("Loading audio...", file=sys.stderr)
    X, y, spk = load(args.corpus, args.speaker_regex, args.limit)

    n_spk = len(np.unique(spk))
    print("\n%d clips, %d real, %d fake, %d speakers"
          % (len(y), (y == 0).sum(), (y == 1).sum(), n_spk))
    if n_spk < 4:
        print("WARNING: only %d speakers - the test score will not mean much."
              % n_spk)

    tr, te = split_by_speaker(spk, args.test_frac)
    if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
        sys.exit("Split put all of one class on one side. Need real AND fake "
                 "clips from several different speakers.")

    print("train %d clips / test %d clips (no shared speakers)"
          % (tr.sum(), te.sum()))

    model = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.1, random_state=0)
    model.fit(X[tr], y[tr])

    scores = model.predict_proba(X[te])[:, 1]
    rate, thr = eer(y[te], scores)
    acc = ((scores > 0.5) == y[te]).mean()

    print("\n=== held-out test (speakers the model never heard) ===")
    print("  EER      %5.2f%%   (lower better; <5%% is decent, 50%% is a coin flip)"
          % (rate * 100))
    print("  accuracy %5.2f%%   at the default 0.5 cutoff" % (acc * 100))
    print("  best cutoff for balanced errors: %.3f" % thr)

    if args.save:
        joblib.dump({"model": model, "threshold": thr,
                     "sample_rate": SAMPLE_RATE, "max_seconds": MAX_SECONDS,
                     "eer": rate,
                     # provenance, so a renamed .pkl still says what it saw
                     "corpus": str(args.corpus),
                     "clips": int(len(y)),
                     "speakers": int(len(set(spk))),
                     "trained_at": time.time()}, args.save)
        print("\nSaved %s" % args.save)
    return rate


def predict(model_path, clips):
    bundle = joblib.load(model_path)
    model, thr = bundle["model"], bundle["threshold"]
    print("(model EER on its own test set: %.2f%%)\n" % (bundle["eer"] * 100))
    for clip in clips:
        p = float(model.predict_proba(features(clip)[None, :])[0, 1])
        print("%-50s  fake-probability %.3f  -> %s"
              % (Path(clip).name, p, "FAKE" if p > thr else "REAL"))


def evaluate(model_path, corpus, limit=None):
    """Score a whole labelled corpus with an existing model.

    This is the honest generalisation test: train on one dataset, evaluate on
    another (or on a held-out split the model never saw). --predict answers
    one clip; this answers "how good is this model, really"."""
    bundle = joblib.load(model_path)
    model, thr = bundle["model"], bundle["threshold"]

    X, y, _ = load(corpus, r"([A-Za-z]*\d+)", limit)
    if not len(X):
        sys.exit("No clips under %s (expects real/ and fake/ inside)" % corpus)

    p = model.predict_proba(X)[:, 1]
    rate, _ = eer(y, p)
    pred = (p > thr).astype(int)

    # per class, because one number hides which side is failing
    print("\n=== %s on %s ===" % (Path(model_path).name, corpus))
    print("  %d clips, %d real, %d fake" % (len(y), (y == 0).sum(), (y == 1).sum()))
    print("  EER       %.2f%%   (model reported %.2f%% on its own test set)"
          % (rate * 100, bundle.get("eer", 0) * 100))
    print("  accuracy  %.2f%%   at cutoff %.3f"
          % ((pred == y).mean() * 100, thr))
    for name, want in (("real", 0), ("fake", 1)):
        m = y == want
        if m.any():
            print("  %-4s recall %.2f%%  (%d of %d)"
                  % (name, (pred[m] == want).mean() * 100,
                     (pred[m] == want).sum(), m.sum()))
    return rate


def _selftest():
    """Synthetic 'real' vs 'fake' with a deliberate difference (the fakes are
    band-limited, like a vocoder). Fails if the pipeline stops separating them
    or if speaker leakage sneaks back in."""
    import tempfile
    import soundfile as sf
    from scipy.signal import butter, lfilter

    tmp = Path(tempfile.mkdtemp())
    (tmp / "real").mkdir()
    (tmp / "fake").mkdir()
    rng = np.random.default_rng(0)
    b, a = butter(8, 2500 / (SAMPLE_RATE / 2), btype="low")

    for spk_id in range(8):
        for take in range(4):
            sig = 0.2 * rng.standard_normal(SAMPLE_RATE * 2)
            sig *= np.abs(np.sin(np.linspace(0, 6 * np.pi, sig.size)))
            name = "id%03d-%02d.wav" % (spk_id, take)
            sf.write(tmp / "real" / name, sig.astype(np.float32), SAMPLE_RATE)
            sf.write(tmp / "fake" / name,
                     lfilter(b, a, sig).astype(np.float32), SAMPLE_RATE)

    # speaker splitting must keep every speaker on one side
    _, _, spk = load(tmp, r"(id\d+)", None)
    tr, te = split_by_speaker(spk, 0.3)
    assert not (set(spk[tr]) & set(spk[te])), "speaker leaked across the split"
    assert tr.sum() and te.sum()

    args = argparse.Namespace(corpus=tmp, speaker_regex=r"(id\d+)", limit=None,
                              test_frac=0.3, save=None)
    rate = train(args)
    assert rate < 0.25, "band-limited fakes should be easy; got EER %.3f" % rate

    # eer() sanity: perfectly separable scores -> 0
    assert eer(np.array([0, 0, 1, 1]), np.array([.1, .2, .8, .9]))[0] == 0.0

    # the pyav fallback must produce the same audio librosa does, or half of
    # ASVspoof 2021 DF silently disappears again
    clip = next((tmp / "real").glob("*.wav"))
    good, _ = _load_audio(clip)
    real_load = librosa.load
    librosa.load = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("simulated flac decoder failure"))
    try:
        fallback, sr = _load_audio(clip)
    finally:
        librosa.load = real_load
    assert sr == SAMPLE_RATE, sr
    n = min(len(good), len(fallback))
    assert n > SAMPLE_RATE // 2, "fallback returned almost nothing: %d" % n
    assert np.abs(good[:n] - fallback[:n]).max() < 1e-4, "fallback audio differs"

    # evaluate() must agree with the score train() just produced on the same
    # clips, or the two paths have drifted apart
    saved = tmp / "m.pkl"
    args = argparse.Namespace(corpus=tmp, speaker_regex=r"(id\d+)", limit=None,
                              test_frac=0.3, save=saved)
    train(args)
    assert saved.is_file()
    got = evaluate(saved, tmp)
    assert 0.0 <= got <= 1.0, got

    print("\ntrain_detector selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", nargs="?", help="folder with real/ and fake/ inside")
    ap.add_argument("--limit", type=int, help="max clips per class")
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--speaker-regex", default=r"([A-Za-z]*\d+)",
                    help="capture group 1 of the filename = speaker id")
    ap.add_argument("--save", help="write the trained model here")
    ap.add_argument("--predict", nargs="+", metavar=("MODEL", "CLIP"),
                    help="MODEL.pkl CLIP [CLIP...]")
    ap.add_argument("--evaluate", nargs=2, metavar=("MODEL", "CORPUS"),
                    help="score a labelled corpus (real/ + fake/) with MODEL.pkl")
    args = ap.parse_args()

    if args.evaluate:
        evaluate(args.evaluate[0], args.evaluate[1], args.limit)
        return

    if args.predict:
        predict(args.predict[0], args.predict[1:])
    elif args.corpus:
        train(args)
    else:
        ap.error("give a corpus folder, or --predict MODEL CLIP")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
