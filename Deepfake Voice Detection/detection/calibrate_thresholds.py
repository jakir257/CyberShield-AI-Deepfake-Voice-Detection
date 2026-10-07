"""Measure what genuine speech actually scores, so the thresholds in
cyber/audio_features.py stop being guesses.

Point it at a folder of REAL human speech (VoxCeleb2, or your own recordings):

    python calibrate_thresholds.py path/to/voxceleb2 --limit 3000

It prints, for each threshold in assess(), the percentile of genuine clips that
would trip it - i.e. your false-positive rate on that rule - and suggests a
value that fires on only the rarest few percent of real audio.

This does NOT detect deepfakes and cannot: every clip here is real. It only
tells you how often you cry wolf. See PROJECT_EXPLAINED.md.
"""

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cyber.audio_features import extract_features, assess  # noqa: E402

AUDIO_EXT = {".wav", ".flac", ".m4a", ".mp3", ".ogg", ".aac", ".opus"}

# (feature key, direction, current threshold, points) - mirrors assess().
# "low" means the rule fires when the value is BELOW the threshold.
RULES = [
    ("bandwidth_ratio",   "low",  0.55, 25),
    ("bandwidth_ratio",   "low",  0.80, 10),
    ("dynamic_range_db",  "low",  12.0, 20),
    ("silence_ratio",     "low",  0.02, 15),
    ("mfcc_variance",     "low",  25.0, 20),
    ("spectral_flatness", "high", 0.35, 10),
    ("peak_dbfs",         "high", -0.2, 10),
]


def find_audio(root, limit, seed=0):
    """Sampled so a 1M-file corpus doesn't mean a full walk before any work."""
    files = []
    for path in Path(root).rglob("*"):
        if path.suffix.lower() in AUDIO_EXT:
            files.append(path)
            # keep walking a bit past the limit so the sample isn't just
            # whatever the filesystem happened to list first
            if len(files) >= limit * 20:
                break
    random.Random(seed).shuffle(files)
    return files[:limit]


def measure(files):
    rows, scores, failed = [], [], 0
    for i, path in enumerate(files, 1):
        try:
            f = extract_features(str(path))
        except Exception:
            failed += 1
            continue
        rows.append(f)
        scores.append(assess(f)["score"])
        if i % 100 == 0:
            print("  %d/%d" % (i, len(files)), file=sys.stderr)
    return rows, scores, failed


def report(rows, scores, fp_target):
    n = len(rows)
    print("\nGenuine speech measured: %d clips\n" % n)

    print("=== per-rule false-positive rate (how often each rule fires on REAL audio) ===")
    suggestions = []
    for key, direction, current, points in RULES:
        vals = np.array([r[key] for r in rows], dtype=float)
        if direction == "low":
            fires = float((vals < current).mean())
            # fire on only the bottom fp_target of genuine clips
            suggested = float(np.percentile(vals, fp_target * 100))
        else:
            fires = float((vals > current).mean())
            suggested = float(np.percentile(vals, (1 - fp_target) * 100))

        flag = "  <-- too noisy" if fires > fp_target else ""
        print("%-18s %-4s %8.3g  fires on %5.1f%% of real audio (+%d pts)%s"
              % (key, direction, current, fires * 100, points, flag))
        print("%-18s %-4s %8.3g  <- suggested (%.0f%% FP)"
              % ("", "", suggested, fp_target * 100))
        suggestions.append({
            "feature": key, "direction": direction, "current": current,
            "current_fp_rate": round(fires, 4), "suggested": round(suggested, 4),
        })

    print("\n=== overall score on genuine speech ===")
    s = np.array(scores)
    for p in (50, 75, 90, 95, 99):
        print("  p%-3d %3.0f" % (p, np.percentile(s, p)))
    print("  medium (>=30): %5.1f%% of real audio" % ((s >= 30).mean() * 100))
    print("  high   (>=55): %5.1f%% of real audio" % ((s >= 55).mean() * 100))
    return suggestions


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", help="folder of genuine speech")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--fp-target", type=float, default=0.05,
                    help="acceptable false-positive rate per rule (default 0.05)")
    ap.add_argument("--json", help="write suggestions here")
    args = ap.parse_args()

    files = find_audio(args.corpus, args.limit)
    if not files:
        sys.exit("No audio found under %s (looked for %s)"
                 % (args.corpus, ", ".join(sorted(AUDIO_EXT))))

    print("Measuring %d clips..." % len(files), file=sys.stderr)
    rows, scores, failed = measure(files)
    if not rows:
        sys.exit("Every file failed to decode. Is ffmpeg on PATH for m4a/mp3?")
    if failed:
        print("(%d files failed to decode, skipped)" % failed, file=sys.stderr)

    suggestions = report(rows, scores, args.fp_target)

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"clips": len(rows), "fp_target": args.fp_target,
             "rules": suggestions}, indent=2))
        print("\nWrote %s" % args.json)

    print("\nEvery clip above was REAL. This measures false positives only -")
    print("it says nothing about whether deepfakes would be caught.")


def _selftest():
    """Synthetic corpus with known shape - fails if the percentile math breaks."""
    import tempfile
    import soundfile as sf
    from cyber.audio_features import SAMPLE_RATE

    tmp = Path(tempfile.mkdtemp())
    rng = np.random.default_rng(0)
    for i in range(12):
        # speech-ish: broadband, varying level, real pauses
        sig = 0.2 * rng.standard_normal(SAMPLE_RATE * 2)
        sig *= np.abs(np.sin(np.linspace(0, 6 * np.pi, sig.size)))
        sig[SAMPLE_RATE // 2: SAMPLE_RATE] = 0.0
        sf.write(tmp / ("c%02d.wav" % i), sig.astype(np.float32), SAMPLE_RATE)

    files = find_audio(tmp, 50)
    assert len(files) == 12, files
    rows, scores, failed = measure(files)
    assert failed == 0 and len(rows) == 12

    sugg = report(rows, scores, 0.05)
    assert len(sugg) == len(RULES)
    for s in sugg:
        assert 0.0 <= s["current_fp_rate"] <= 1.0, s
        assert np.isfinite(s["suggested"]), s

    # a rule nothing trips must suggest a threshold no looser than current
    quiet = [s for s in sugg if s["current_fp_rate"] == 0.0 and s["direction"] == "low"]
    for s in quiet:
        assert s["suggested"] >= s["current"], s

    print("\ncalibrate_thresholds selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
