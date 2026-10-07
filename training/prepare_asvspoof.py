"""Sort ASVspoof 2019 LA into the real/ and fake/ folders train_detector.py wants.

ASVspoof ships every clip in one flat folder and keeps the labels in a separate
protocol .txt. This reads the protocol and links each clip into place.

    python prepare_asvspoof.py raw/LA --out corpus

Expects the standard layout inside raw/LA:

    ASVspoof2019_LA_train/flac/*.flac
    ASVspoof2019_LA_cm_protocols/ASVspoof2019.LA.cm.train.trn.txt

Protocol lines look like:

    LA_0079 LA_T_1138215 - - bonafide
    LA_0098 LA_T_1234567 - A01 spoof
      ^speaker ^clip                ^label

Fake-or-Real (FoR) is already sorted into real/ and fake/, so it needs no
protocol at all -- see prepare_folders().

Or skip the paths entirely -- --dataset finds either one and can train it:

    python prepare_asvspoof.py --dataset            # what is on disk?
    python prepare_asvspoof.py --dataset la --train
    python prepare_asvspoof.py --dataset for --train

Hard links by default, so a 5 GB dataset is not copied twice. --copy if the
dataset lives on another drive.
"""

import argparse
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

# the three official splits; train is enough to start
SPLITS = {
    "train": ("ASVspoof2019_LA_train", "ASVspoof2019.LA.cm.train.trn.txt"),
    "dev": ("ASVspoof2019_LA_dev", "ASVspoof2019.LA.cm.dev.trl.txt"),
    "eval": ("ASVspoof2019_LA_eval", "ASVspoof2019.LA.cm.eval.trl.txt"),
}
PROTO_DIR = "ASVspoof2019_LA_cm_protocols"

# what train_detector.py can read; FoR mixes wav and mp3 in the same folder
AUDIO_EXT = {".wav", ".flac", ".m4a", ".mp3", ".ogg", ".opus"}


# --dataset: where each corpus usually sits, and how to train it. Paths are
# tried in order, relative to this file's parent and its parent.
DATASETS = {
    "la": {
        "name": "ASVspoof 2019 LA",
        "find": ["LA", "raw/LA", "training/raw/LA"],
        "ready": "ASVspoof2019_LA_cm_protocols",
        "out": "corpus",
        "regex": r"(LA_\d+)",
        "note": "full-band studio audio; the bandwidth trap applies",
        # where the data came from, so the dashboard can link it
        "url": "https://datashare.ed.ac.uk/handle/10283/3336",
        "download": "LA.zip (~23 GB), unzip into raw/",
    },
    "for": {
        "name": "Fake-or-Real (FoR)",
        "find": ["for-original/for-original", "for-original",
                 "raw/for-original/for-original", "raw/for-original"],
        "ready": "training",
        "out": "corpus_for",
        # filenames are file123.wav with no speaker id, so every clip is its
        # own speaker - same caveat as 2021 DF
        "regex": r"(file\d+)",
        # already sorted into real/ and fake/; no protocol to parse
        "folders": {"real": "training/real", "fake": "training/fake"},
        "note": "balanced 26,941 per class; real is all wav, fake is 88% mp3",
        "url": "https://bil.eecs.yorku.ca/datasets/",
        "download": "for-original.zip, unzip into the repo root",
    },
}

# repo root and training/ both get searched, so either cwd works
SEARCH_ROOTS = [Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent]


def _first_existing(candidates, must_contain=None):
    """-> the first candidate that exists under any search root, else None."""
    for root in SEARCH_ROOTS:
        for rel in candidates:
            p = root / rel
            if p.is_dir() if must_contain is None else (p / must_contain).exists():
                return p
            if must_contain is None and p.is_file():
                return p
    return None


def locate(key):
    """-> (root, keys_path_or_None, why_not). why_not is None when trainable.

    keys is always None now; the slot is kept because prepare() and the
    dashboard both still pass it through."""
    spec = DATASETS[key]
    root = _first_existing(spec["find"], spec["ready"])
    if root is None:
        return None, None, "not found (looked for %s/)" % ", ".join(spec["find"])
    return root, None, None


def show_datasets():
    """Print what is on disk. No arguments = this."""
    print("dataset  status")
    for key, spec in DATASETS.items():
        root, keys, why = locate(key)
        mark = "READY" if why is None else "--"
        print("\n  %-6s %-6s %s" % (key, mark, spec["name"]))
        print("         %s" % spec["note"])
        if why:
            print("         %s" % why)
        else:
            print("         %s" % root)
    print("\nTrain one:  python prepare_asvspoof.py --dataset la --train")


def run_dataset(key, train, limit, copy):
    """Prepare the named dataset, then optionally train on it."""
    spec = DATASETS[key]
    root, keys, why = locate(key)
    if why:
        sys.exit("%s: %s" % (spec["name"], why))

    out = SEARCH_ROOTS[0] / spec["out"]
    print("=> %s\n   %s -> %s\n" % (spec["name"], root, out))
    if "folders" in spec:
        prepare_folders(root, out, spec["folders"], copy, limit)
    else:
        prepare(root, out, ["train"], copy, limit, keys)

    if not train:
        return
    detector = (SEARCH_ROOTS[1] / "Deepfake Voice Detection" / "detection"
                / "train_detector.py")
    if not detector.is_file():
        sys.exit("train_detector.py not at %s" % detector)
    cmd = [sys.executable, str(detector), str(out),
           "--speaker-regex", spec["regex"],
           "--save", str(SEARCH_ROOTS[0] / ("model_%s.pkl" % key))]
    if limit:
        cmd += ["--limit", str(limit)]
    print("\n=> training\n   %s\n" % " ".join(cmd))
    sys.exit(subprocess.call(cmd))


def read_protocol(path):
    """-> [(speaker, clip_id, label)]. Ignores blank and short lines.

    2019 LA puts the label last:
        LA_0079 LA_T_1138215 - - bonafide
    The label is found by value rather than by column, so a protocol with
    extra trailing fields still parses.
    """
    rows = []
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        label = next((p for p in parts if p in ("bonafide", "spoof")), None)
        if label is None:
            continue
        speaker, clip_id = parts[0], parts[1]
        rows.append((speaker, clip_id, label))
    return rows


def _limit_across_speakers(rows, limit):
    """-> up to `limit` rows per class, spread over as many speakers as
    possible. Taking the first N instead would land them all on one speaker
    (the protocol is speaker-ordered) and the train/test split would fail."""
    out = []
    for want in ("bonafide", "spoof"):
        by_speaker = {}
        for row in rows:
            if row[2] == want:
                by_speaker.setdefault(row[0], []).append(row)
        # one clip from each speaker, then a second from each, until full
        picked, depth = [], 0
        while len(picked) < limit:
            layer = [v[depth] for v in by_speaker.values() if depth < len(v)]
            if not layer:
                break
            picked.extend(layer[:limit - len(picked)])
            depth += 1
        out.extend(picked)
    return out


def link(src, dst, copy):
    if dst.exists():
        return
    if copy:
        shutil.copy2(src, dst)
        return
    try:
        os.link(src, dst)
    except OSError:
        # different volume, or a filesystem without hard links
        shutil.copy2(src, dst)


def prepare_folders(root, out, folders, copy, limit):
    """For a dataset that is already sorted into real/ and fake/ (FoR).
    There is no protocol to read - link what is in each folder."""
    root, out = Path(root), Path(out)
    (out / "real").mkdir(parents=True, exist_ok=True)
    (out / "fake").mkdir(parents=True, exist_ok=True)

    totals = Counter()
    for side, rel in folders.items():
        src_dir = root / rel
        if not src_dir.is_dir():
            sys.exit("No %s under %s" % (rel, root))
        files = sorted(f for f in src_dir.iterdir()
                       if f.suffix.lower() in AUDIO_EXT)
        if limit:
            files = files[:limit]
        for f in files:
            # keep the extension: this corpus mixes wav and mp3
            link(f, out / side / f.name, copy)
            totals[side] += 1
        print("%-5s %6d clips" % (side, totals[side]))

    print("\ncorpus/real  %6d" % totals["real"])
    print("corpus/fake  %6d" % totals["fake"])
    if not totals:
        sys.exit("Nothing linked - check the dataset path.")
    return totals


def prepare(root, out, splits, copy, limit, keys=None):
    """Sort ASVspoof 2019 LA using its protocol files.

    keys is accepted and ignored; callers still pass locate()'s middle value."""
    root, out = Path(root), Path(out)

    table = SPLITS
    proto_dir = root / PROTO_DIR
    if not proto_dir.is_dir():
        sys.exit("No %s under %s\nPoint this at the folder that contains "
                 "ASVspoof2019_LA_train/ and %s/" % (PROTO_DIR, root, PROTO_DIR))

    (out / "real").mkdir(parents=True, exist_ok=True)
    (out / "fake").mkdir(parents=True, exist_ok=True)

    totals = Counter()
    for split in splits:
        folder, proto_name = table[split]
        flac_dir = root / folder / "flac"
        proto = proto_dir / proto_name
        if not flac_dir.is_dir():
            print("skip %-5s - no %s" % (split, flac_dir), file=sys.stderr)
            continue
        if not proto.is_file():
            print("skip %-5s - no %s" % (split, proto), file=sys.stderr)
            continue

        rows = read_protocol(proto)
        if limit:
            # keep the real/fake balance while trimming: ASVspoof is ~10:1 spoof.
            # The protocol is grouped by speaker, so taking the first N lands
            # every clip on one speaker and train_detector refuses to split.
            # Round-robin over speakers instead.
            rows = _limit_across_speakers(rows, limit)

        missing = 0
        for speaker, clip_id, label in rows:
            src = flac_dir / (clip_id + ".flac")
            if not src.is_file():
                missing += 1
                continue
            side = "real" if label == "bonafide" else "fake"
            if speaker == "-":
                speaker = clip_id      # no speaker column: keep the clip alone
            # speaker id first so train_detector's --speaker-regex can find it
            link(src, out / side / ("%s-%s.flac" % (speaker, clip_id)), copy)
            totals[side] += 1

        print("%-5s %6d clips%s" % (split, len(rows),
              "  (%d files missing)" % missing if missing else ""))

    print("\ncorpus/real  %6d" % totals["real"])
    print("corpus/fake  %6d" % totals["fake"])
    if not totals:
        sys.exit("Nothing linked - check the dataset path.")
    if totals["fake"] > totals["real"] * 5:
        print("\nNote: ASVspoof is ~9x more spoof than bonafide. That is normal,\n"
              "but EER is the metric to trust, not accuracy.")
    print("\nNow:  python ../\"Deepfake Voice Detection\"/detection/train_detector.py"
          " %s --speaker-regex \"(LA_\\d+)\" --save model.pkl" % out)


def _selftest():
    """Fake mini-dataset in the real ASVspoof shape. Fails if the protocol
    parsing or the label routing breaks."""
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    flac = tmp / "ASVspoof2019_LA_train" / "flac"
    flac.mkdir(parents=True)
    (tmp / PROTO_DIR).mkdir()

    lines = []
    for i in range(6):
        label = "bonafide" if i % 2 == 0 else "spoof"
        clip = "LA_T_%07d" % i
        (flac / (clip + ".flac")).write_bytes(b"not really flac")
        lines.append("LA_%04d %s - %s %s" % (i, clip, "-" if i % 2 == 0 else "A01", label))
    lines.append("")                     # blank line must be skipped
    lines.append("garbage short line")   # malformed must be skipped
    (tmp / PROTO_DIR / SPLITS["train"][1]).write_text("\n".join(lines))

    rows = read_protocol(tmp / PROTO_DIR / SPLITS["train"][1])
    assert len(rows) == 6, rows
    assert sum(1 for r in rows if r[2] == "bonafide") == 3, rows

    out = tmp / "corpus"
    prepare(tmp, out, ["train"], copy=True, limit=None)
    assert len(list((out / "real").glob("*.flac"))) == 3
    assert len(list((out / "fake").glob("*.flac"))) == 3
    # speaker id must lead the filename, else the split leaks
    assert all(p.name.startswith("LA_") for p in (out / "real").glob("*.flac"))

    prepare(tmp, out, ["train"], copy=True, limit=None)  # rerun must be safe
    assert len(list((out / "real").glob("*.flac"))) == 3

    # --limit must spread over speakers, not take the first N of a
    # speaker-ordered protocol (that yields 1 speaker and an unsplittable set)
    grouped = []
    for s in range(4):
        for c in range(10):
            grouped.append(("LA_%04d" % s, "clip%d_%d" % (s, c), "bonafide"))
            grouped.append(("LA_%04d" % s, "spf%d_%d" % (s, c), "spoof"))
    picked = _limit_across_speakers(grouped, 8)
    assert len(picked) == 16, len(picked)          # 8 per class
    for want in ("bonafide", "spoof"):
        spk = {r[0] for r in picked if r[2] == want}
        assert len(spk) == 4, "limit collapsed to %d speaker(s)" % len(spk)
    # asking for more than exists must not hang or over-deliver
    assert len(_limit_across_speakers(grouped, 999)) == 80

    # --- FoR: already sorted into real/ and fake/, mixed wav and mp3
    fr = Path(tempfile.mkdtemp())
    for side, n in (("real", 5), ("fake", 4)):
        d = fr / "training" / side
        d.mkdir(parents=True)
        for i in range(n):
            ext = ".mp3" if side == "fake" and i % 2 else ".wav"
            (d / ("file%d%s" % (i, ext))).write_bytes(b"not really audio")
        (d / "notes.txt").write_bytes(b"must be ignored")   # non-audio

    out_fr = fr / "corpus_for"
    totals = prepare_folders(fr, out_fr, DATASETS["for"]["folders"],
                             copy=True, limit=None)
    assert totals["real"] == 5, totals
    assert totals["fake"] == 4, totals            # notes.txt skipped
    assert len(list((out_fr / "fake").glob("*.mp3"))) == 2, "mp3 must survive"
    prepare_folders(fr, out_fr, DATASETS["for"]["folders"],
                    copy=True, limit=None)        # rerun must be safe
    assert totals["real"] == 5

    # the label is found by value, so trailing fields after it still parse
    extra = tmp / "extra.txt"
    extra.write_text("LA_0001 LA_T_9999999 - A01 spoof notrim eval\n"
                     "LA_0002 LA_T_9999998 - - bonafide notrim eval\n")
    rows = read_protocol(extra)
    assert len(rows) == 2, rows
    assert sum(1 for r in rows if r[2] == "bonafide") == 1, rows

    print("\nprepare_asvspoof selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", help="the LA/ folder from the download")
    ap.add_argument("--out", default="corpus")
    ap.add_argument("--splits", nargs="+", default=["train"], choices=list(SPLITS))
    ap.add_argument("--limit", type=int, help="max clips per class per split")
    ap.add_argument("--copy", action="store_true",
                    help="copy instead of hard-link (needed across drives)")
    ap.add_argument("--dataset", nargs="?", const="", choices=["", *DATASETS],
                    help="find a dataset on disk and prepare it; "
                         "bare --dataset lists what is available")
    ap.add_argument("--train", action="store_true",
                    help="with --dataset: run train_detector.py afterwards")
    args = ap.parse_args()

    if args.dataset is not None:
        if not args.dataset:
            show_datasets()
            return
        run_dataset(args.dataset, args.train, args.limit, args.copy)
        return

    if not args.root:
        ap.error("give the path to the LA/ folder, "
                 "or use --dataset to pick one")
    prepare(args.root, args.out, args.splits, args.copy, args.limit)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
