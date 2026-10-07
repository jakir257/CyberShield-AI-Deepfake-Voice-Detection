"""
Real acoustic measurements extracted from the processed audio.

IMPORTANT, and stated plainly because the UI used to lie about this:
this module is NOT a deepfake classifier. There is no trained model here.
It measures properties of the signal that are genuinely computed, and applies
a small set of readable heuristics that are known to correlate with synthetic
or re-encoded audio. Every number the dashboard shows comes from here, and
every flag carries the reason it fired.

A high indicator score means "this recording has properties often seen in
synthesised or heavily processed audio" - NOT "this is a deepfake".
"""

import librosa
import numpy as np

SAMPLE_RATE = 16000
N_FFT = 1024
HOP = 256


def _safe(value, digits=3):
    """librosa can hand back nan/inf on degenerate input."""
    v = float(value)
    if not np.isfinite(v):
        return 0.0
    return round(v, digits)


def extract_features(path, sample_rate=SAMPLE_RATE):
    """Measure one audio file. Returns plain floats ready for JSON."""

    audio, sr = librosa.load(path, sr=sample_rate, mono=True)

    if audio is None or len(audio) == 0:
        raise ValueError("Audio is empty.")

    nyquist = sr / 2.0

    stft = np.abs(librosa.stft(audio, n_fft=N_FFT, hop_length=HOP))

    # ---- spectral shape -------------------------------------------------
    centroid = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
    rolloff95 = librosa.feature.spectral_rolloff(S=stft, sr=sr, roll_percent=0.95)[0]
    flatness = librosa.feature.spectral_flatness(S=stft)[0]
    zcr = librosa.feature.zero_crossing_rate(audio, hop_length=HOP)[0]

    # ---- effective bandwidth --------------------------------------------
    # highest bin still carrying 1/1000 of peak energy, averaged over time.
    # Vocoders and low-bitrate codecs leave a hard ceiling here.
    mean_spectrum = stft.mean(axis=1)
    peak = mean_spectrum.max() if mean_spectrum.size else 0.0
    if peak > 0:
        above = np.where(mean_spectrum > peak * 0.001)[0]
        top_bin = int(above[-1]) if above.size else 0
    else:
        top_bin = 0
    bandwidth = top_bin * sr / N_FFT

    # ---- level dynamics --------------------------------------------------
    frame = 512
    usable = len(audio) - (len(audio) % frame)
    if usable >= frame:
        frames = audio[:usable].reshape(-1, frame)
        rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-10
        rms_db = 20 * np.log10(rms)
        dynamic_range = float(np.percentile(rms_db, 95) - np.percentile(rms_db, 5))
        silence_ratio = float((rms < 10 ** (-50 / 20)).mean())
    else:
        dynamic_range = 0.0
        silence_ratio = 0.0

    peak_amp = float(np.abs(audio).max())
    rms_all = float(np.sqrt((audio ** 2).mean()))
    crest = 20 * np.log10((peak_amp + 1e-10) / (rms_all + 1e-10))

    # ---- timbre variation ------------------------------------------------
    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=13)
    mfcc_var = float(np.mean(np.var(mfcc, axis=1)))

    return {
        "duration": _safe(len(audio) / sr, 2),
        "sample_rate": int(sr),
        "nyquist": int(nyquist),
        "bandwidth_hz": int(bandwidth),
        "bandwidth_ratio": _safe(bandwidth / nyquist if nyquist else 0),
        "spectral_centroid_hz": int(np.mean(centroid)),
        "spectral_rolloff95_hz": int(np.mean(rolloff95)),
        "spectral_flatness": _safe(np.mean(flatness), 4),
        "zero_crossing_rate": _safe(np.mean(zcr), 4),
        "dynamic_range_db": _safe(dynamic_range, 1),
        "crest_factor_db": _safe(crest, 1),
        "silence_ratio": _safe(silence_ratio, 3),
        "mfcc_variance": _safe(mfcc_var, 1),
        "peak_dbfs": _safe(20 * np.log10(peak_amp + 1e-10), 1),
    }


def assess(features):
    """Readable heuristics over the measurements. Returns score/level/notes.

    Kept conservative on purpose: band-limited audio is normal for telephony,
    so a narrow bandwidth alone is reported, not treated as proof of anything.
    """

    notes = []
    score = 0

    bw_ratio = features["bandwidth_ratio"]
    bandwidth = features["bandwidth_hz"]

    if bw_ratio < 0.55:
        score += 25
        notes.append(
            "energy stops at %d Hz of a possible %d Hz - typical of a vocoder "
            "or a low-bitrate codec (also normal for phone-call audio)"
            % (bandwidth, features["nyquist"])
        )
    elif bw_ratio < 0.8:
        score += 10
        notes.append(
            "bandwidth limited to %d Hz, below the %d Hz the sample rate allows"
            % (bandwidth, features["nyquist"])
        )

    if features["dynamic_range_db"] < 12:
        score += 20
        notes.append(
            "level is unusually uniform (%.1f dB spread) - natural speech "
            "varies more" % features["dynamic_range_db"]
        )

    if features["silence_ratio"] < 0.02:
        score += 15
        notes.append(
            "almost no silence (%.1f%%) - synthesised speech often lacks "
            "natural pauses" % (features["silence_ratio"] * 100)
        )

    if features["mfcc_variance"] < 25:
        score += 20
        notes.append(
            "timbre barely varies (MFCC variance %.1f) - a marker of "
            "synthetic delivery" % features["mfcc_variance"]
        )

    if features["spectral_flatness"] > 0.35:
        score += 10
        notes.append(
            "spectrum is unusually noise-like (flatness %.3f)"
            % features["spectral_flatness"]
        )

    if features["peak_dbfs"] > -0.2:
        score += 10
        notes.append(
            "signal is clipped at full scale (%.1f dBFS) - measurements below "
            "are less reliable" % features["peak_dbfs"]
        )

    score = min(score, 100)

    if score >= 55:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"

    if not notes:
        notes.append("no synthetic-audio indicators matched")

    return {"score": score, "level": level, "notes": notes}


def analyse_file(path, sample_rate=SAMPLE_RATE):
    features = extract_features(path, sample_rate)
    verdict = assess(features)
    result = {
        "features": features,
        "indicator_score": verdict["score"],
        "indicator_level": verdict["level"],
        "indicator_notes": verdict["notes"],
        # said out loud so the UI cannot quietly imply otherwise
        "is_model_prediction": False,
    }

    # A trained model, if the dashboard has produced one, is the actual
    # classifier. The heuristics above stay: they are readable, and they are
    # the fallback when nothing has been trained yet.
    try:
        from .model_training import predict
        prediction = predict(path)
    except Exception:
        prediction = None

    if prediction:
        result["model"] = prediction
        result["is_model_prediction"] = True

    return result


def _selftest():
    """Fails if the measurements stop distinguishing obviously different signals."""
    import os
    import tempfile
    import soundfile as sf

    tmp = tempfile.mkdtemp()

    # 1. pure tone: narrow band, no silence, flat level -> should score high
    t = np.linspace(0, 3, SAMPLE_RATE * 3, endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    tone_path = os.path.join(tmp, "tone.wav")
    sf.write(tone_path, tone, SAMPLE_RATE)

    tone_r = analyse_file(tone_path)
    assert tone_r["features"]["bandwidth_hz"] < 2000, tone_r["features"]
    assert tone_r["indicator_level"] in ("medium", "high"), tone_r
    assert tone_r["is_model_prediction"] is False

    # 2. white noise: full bandwidth, high flatness
    rng = np.random.default_rng(0)
    noise = (0.15 * rng.standard_normal(SAMPLE_RATE * 3)).astype(np.float32)
    noise_path = os.path.join(tmp, "noise.wav")
    sf.write(noise_path, noise, SAMPLE_RATE)

    noise_r = analyse_file(noise_path)
    assert noise_r["features"]["bandwidth_hz"] > 6000, noise_r["features"]
    assert noise_r["features"]["spectral_flatness"] > \
        tone_r["features"]["spectral_flatness"], "noise must be flatter than a tone"

    # 3. bandwidth measurement must track a real low-pass
    from scipy.signal import butter, lfilter
    b, a = butter(8, 3000 / (SAMPLE_RATE / 2), btype="low")
    filtered = lfilter(b, a, noise).astype(np.float32)
    filt_path = os.path.join(tmp, "filtered.wav")
    sf.write(filt_path, filtered, SAMPLE_RATE)

    filt_r = analyse_file(filt_path)
    assert filt_r["features"]["bandwidth_hz"] < noise_r["features"]["bandwidth_hz"], \
        "low-passed audio must measure a narrower bandwidth"

    # 4. silence must be detected as silence
    with_gaps = noise.copy()
    with_gaps[SAMPLE_RATE:SAMPLE_RATE * 2] = 0.0
    gap_path = os.path.join(tmp, "gaps.wav")
    sf.write(gap_path, with_gaps, SAMPLE_RATE)
    gap_r = analyse_file(gap_path)
    assert gap_r["features"]["silence_ratio"] > 0.25, gap_r["features"]

    print("audio_features selftest ok")
    print("  tone     bw=%5d Hz  flat=%.4f  score=%d" % (
        tone_r["features"]["bandwidth_hz"],
        tone_r["features"]["spectral_flatness"], tone_r["indicator_score"]))
    print("  noise    bw=%5d Hz  flat=%.4f  score=%d" % (
        noise_r["features"]["bandwidth_hz"],
        noise_r["features"]["spectral_flatness"], noise_r["indicator_score"]))
    print("  lowpass  bw=%5d Hz" % filt_r["features"]["bandwidth_hz"])
    print("  gaps     silence=%.2f" % gap_r["features"]["silence_ratio"])


if __name__ == "__main__":
    _selftest()
