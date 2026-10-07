"""
Prepare the exact 140,000-clip dataset for Forensics 0.3B fine-tuning.

FINAL DATASET
-------------
REAL:
    50,000 IndicVoices
    18,000 ASVspoof 5 bona fide

FAKE:
    50,000 MLAAD
    22,000 ASVspoof 5 spoof

TOTAL:
    140,000 clips

IndicVoices:
    The audio is embedded directly inside Parquet files as FLAC bytes.
    This script extracts only the selected 50,000 clips.

MLAAD:
    Existing audio files are used directly. No copying.

ASVspoof 5:
    Existing FLAC files are used directly. No copying.

Output:
    training/forensics_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
import re
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq


AUDIO_EXT = {
    ".wav",
    ".flac",
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
}

# Exact dataset targets
INDICVOICES_TARGET = 50_000
MLAAD_TARGET = 50_000
ASVSPOOF_REAL_TARGET = 18_000
ASVSPOOF_FAKE_TARGET = 22_000


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------

def scan_audio(root: Path):
    """Return all supported audio files under root."""
    if not root.exists():
        return []

    return [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXT
    ]


def speaker_from_name(name: str, source: str):
    """
    Best-effort speaker extraction for MLAAD.

    IndicVoices and ASVspoof use explicit speaker metadata elsewhere.
    MLAAD filenames are not guaranteed to contain a speaker ID, so this
    falls back to the filename stem when necessary.
    """
    if source == "asvspoof5":
        m = re.match(r"([TDE]_\d+)", name)
        return m.group(1) if m else Path(name).stem

    m = re.search(r"__spk_([^_]+(?:_[^_]+)*)__", name)

    if m:
        return m.group(1)

    return Path(name).stem


def stable_id(text: str):
    """Short deterministic ID used for generated IndicVoices filenames."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------
# IndicVoices
# ---------------------------------------------------------------------

def find_indicvoices_parquets(root: Path):
    """Find all IndicVoices Parquet files."""
    files = sorted(root.rglob("*.parquet"))

    if not files:
        raise SystemExit(
            f"No Parquet files found under IndicVoices root:\n{root}"
        )

    return files


def collect_indicvoices_metadata(root: Path):
    """
    Read only lightweight metadata from IndicVoices.

    IMPORTANT:
    We deliberately do NOT read audio_filepath during this pass because
    audio_filepath contains the actual FLAC bytes.
    """

    parquet_files = find_indicvoices_parquets(root)

    records = []

    print()
    print("=" * 70)
    print("SCANNING INDICVOICES PARQUET METADATA")
    print("=" * 70)
    print(f"Parquet files found: {len(parquet_files)}")

    total_rows = 0

    for parquet_path in parquet_files:
        pf = pq.ParquetFile(parquet_path)

        row_count = pf.metadata.num_rows
        total_rows += row_count

        print(f"{parquet_path} -> {row_count:,} rows")

        global_row = 0

        # Read only metadata columns.
        available_columns = set(pf.schema_arrow.names)

        columns = []

        if "speaker_id" in available_columns:
            columns.append("speaker_id")

        if "lang" in available_columns:
            columns.append("lang")

        if not columns:
            raise SystemExit(
                f"Required metadata columns not found in:\n{parquet_path}"
            )

        for batch in pf.iter_batches(
            batch_size=512,
            columns=columns,
        ):
            data = batch.to_pydict()

            batch_size = batch.num_rows

            for i in range(batch_size):
                speaker = data.get("speaker_id", [None] * batch_size)[i]
                language = data.get("lang", [None] * batch_size)[i]

                if speaker is None:
                    speaker = f"unknown_{global_row}"

                if language is None:
                    language = parquet_path.parent.name

                records.append(
                    {
                        "parquet": parquet_path,
                        "row_index": global_row,
                        "speaker": str(speaker),
                        "language": str(language),
                    }
                )

                global_row += 1

    print()
    print(f"TOTAL INDICVOICES ROWS FOUND: {total_rows:,}")

    return records


def select_indicvoices(records, target: int, seed: int):
    """Select exactly target IndicVoices records."""

    if len(records) < target:
        raise SystemExit(
            f"Not enough IndicVoices clips.\n"
            f"Available: {len(records):,}\n"
            f"Required: {target:,}"
        )

    rng = random.Random(seed)

    selected = rng.sample(records, target)

    # Stable ordering makes extraction reproducible.
    selected.sort(
        key=lambda r: (
            str(r["parquet"]),
            r["row_index"],
        )
    )

    return selected


def extract_selected_indicvoices(
    selected,
    output_root: Path,
):
    """
    Extract selected IndicVoices FLAC bytes from Parquet.

    Existing extracted files are reused, so rerunning the script does not
    unnecessarily rewrite files.
    """

    print()
    print("=" * 70)
    print("EXTRACTING SELECTED INDICVOICES AUDIO")
    print("=" * 70)

    output_root.mkdir(parents=True, exist_ok=True)

    # Group selected rows by Parquet file.
    selected_by_file = defaultdict(list)

    for record in selected:
        selected_by_file[record["parquet"]].append(record)

    manifest_rows = []

    extracted = 0
    skipped_existing = 0

    for parquet_path, file_records in selected_by_file.items():

        print()
        print(f"Processing: {parquet_path}")
        print(f"Selected rows: {len(file_records):,}")

        selected_indices = {
            r["row_index"]: r
            for r in file_records
        }

        pf = pq.ParquetFile(parquet_path)

        current_row = 0

        for batch in pf.iter_batches(
            batch_size=64,
            columns=["audio_filepath"],
        ):
            audio_values = batch.column(0)

            for i in range(batch.num_rows):

                row_index = current_row + i

                if row_index not in selected_indices:
                    continue

                record = selected_indices[row_index]

                audio_obj = audio_values[i].as_py()

                audio_bytes = None

                if isinstance(audio_obj, dict):
                    audio_bytes = audio_obj.get("bytes")

                elif isinstance(audio_obj, bytes):
                    audio_bytes = audio_obj

                if not audio_bytes:
                    raise RuntimeError(
                        f"No embedded audio bytes found for:\n"
                        f"{parquet_path}\n"
                        f"row {row_index}"
                    )

                language = re.sub(
                    r"[^A-Za-z0-9_-]+",
                    "_",
                    record["language"],
                )

                speaker = re.sub(
                    r"[^A-Za-z0-9_-]+",
                    "_",
                    record["speaker"],
                )

                unique = stable_id(
                    f"{parquet_path}|{row_index}|"
                    f"{record['speaker']}"
                )

                language_dir = output_root / language
                language_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                output_file = (
                    language_dir
                    / f"{speaker}__iv_{unique}.flac"
                )

                if output_file.exists():
                    skipped_existing += 1
                else:
                    with output_file.open("wb") as f:
                        f.write(audio_bytes)

                    extracted += 1

                manifest_rows.append(
                    {
                        "path": str(output_file.resolve()),
                        "label": 1,
                        "source": "indicvoices",
                        "language": language,
                        "speaker": speaker,
                    }
                )

            current_row += batch.num_rows

        print(
            f"Finished {parquet_path.name}"
        )

    print()
    print(f"IndicVoices extracted: {extracted:,}")
    print(f"IndicVoices already existed: {skipped_existing:,}")
    print(f"IndicVoices manifest rows: {len(manifest_rows):,}")

    if len(manifest_rows) != len(selected):
        raise RuntimeError(
            "IndicVoices extraction count does not match selection."
        )

    return manifest_rows


# ---------------------------------------------------------------------
# MLAAD
# ---------------------------------------------------------------------

def read_mlaad(root: Path, target: int, seed: int):
    """
    Select exactly target MLAAD audio files.

    The current directory contains approximately 50,100 files because
    100 test files existed before the 50,000-file download. Therefore
    this function deterministically samples exactly 50,000.
    """

    print()
    print("=" * 70)
    print("SCANNING MLAAD")
    print("=" * 70)

    files = scan_audio(root)

    print(f"MLAAD audio files found: {len(files):,}")

    if len(files) < target:
        raise SystemExit(
            f"Not enough MLAAD audio files.\n"
            f"Available: {len(files):,}\n"
            f"Required: {target:,}"
        )

    files = sorted(files)

    rng = random.Random(seed)

    selected = rng.sample(files, target)

    selected.sort()

    rows = []

    for p in selected:

        try:
            relative = p.relative_to(root)

            if len(relative.parts) >= 1:
                language = relative.parts[0]
            else:
                language = "unknown"

        except Exception:
            language = "unknown"

        rows.append(
            {
                "path": str(p.resolve()),
                "label": 0,
                "source": "mlaad",
                "language": language,
                "speaker": speaker_from_name(
                    p.name,
                    "mlaad",
                ),
            }
        )

    print(f"MLAAD selected: {len(rows):,}")

    return rows


# ---------------------------------------------------------------------
# ASVspoof 5
# ---------------------------------------------------------------------

def read_asvspoof(root: Path):
    """
    Read ASVspoof 5 training protocol.

    Protocol:
        ASVspoof5.train.tsv

    Audio:
        flac_T/*.flac

    The protocol filename may omit '.flac', so both forms are checked.
    """

    print()
    print("=" * 70)
    print("SCANNING ASVSPOOF 5")
    print("=" * 70)

    proto = root / "ASVspoof5.train.tsv"
    audio_root = root / "flac_T"

    if not proto.exists():
        raise SystemExit(
            f"Missing ASVspoof protocol:\n{proto}"
        )

    if not audio_root.exists():
        raise SystemExit(
            f"Missing ASVspoof audio directory:\n{audio_root}"
        )

    rows = []

    with proto.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as f:

        for line in f:

            line = line.strip()

            if not line or line.startswith("#"):
                continue

            cols = line.split()

            if len(cols) < 9:
                continue

            speaker = cols[0]
            filename = cols[1]
            key = cols[8].lower()

            if key == "bonafide":
                label = 1

            elif key == "spoof":
                label = 0

            else:
                continue

            # The protocol usually contains the filename without extension.
            candidates = [
                audio_root / filename,
                audio_root / f"{filename}.flac",
            ]

            audio_path = None

            for candidate in candidates:
                if candidate.exists():
                    audio_path = candidate
                    break

            if audio_path is None:
                continue

            rows.append(
                {
                    "path": str(audio_path.resolve()),
                    "label": label,
                    "source": "asvspoof5",
                    "language": "english",
                    "speaker": speaker,
                }
            )

    real_count = sum(
        r["label"] == 1
        for r in rows
    )

    fake_count = sum(
        r["label"] == 0
        for r in rows
    )

    print(f"ASVspoof usable clips: {len(rows):,}")
    print(f"  bona fide: {real_count:,}")
    print(f"  spoof:     {fake_count:,}")

    return rows


def select_asvspoof(rows, label, target, seed):
    """Select exactly target ASVspoof clips for one class."""

    candidates = [
        r
        for r in rows
        if r["label"] == label
    ]

    if len(candidates) < target:

        class_name = (
            "bona fide"
            if label == 1
            else "spoof"
        )

        raise SystemExit(
            f"Not enough ASVspoof {class_name} clips.\n"
            f"Available: {len(candidates):,}\n"
            f"Required: {target:,}"
        )

    rng = random.Random(seed)

    selected = rng.sample(
        candidates,
        target,
    )

    return selected


# ---------------------------------------------------------------------
# Split assignment
# ---------------------------------------------------------------------

def assign_splits(rows, seed, val_fraction=0.10):
    """
    Assign train/validation split.

    Speakers are kept together where possible.

    If a source has unique speaker identifiers per file, this naturally
    behaves approximately like a random 90/10 split for that source.
    """

    rng = random.Random(seed)

    by_source_speaker = defaultdict(list)

    for row in rows:
        key = (
            row["source"],
            row["speaker"],
        )

        by_source_speaker[key].append(row)

    groups = list(by_source_speaker.keys())

    rng.shuffle(groups)

    validation_group_count = max(
        1,
        int(len(groups) * val_fraction),
    )

    validation_groups = set(
        groups[:validation_group_count]
    )

    for row in rows:

        key = (
            row["source"],
            row["speaker"],
        )

        row["split"] = (
            "val"
            if key in validation_groups
            else "train"
        )

    return rows


# ---------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------

def verify_rows(rows):
    """Verify exact counts and paths."""

    print()
    print("=" * 70)
    print("VERIFYING FINAL DATASET")
    print("=" * 70)

    real = [
        r
        for r in rows
        if r["label"] == 1
    ]

    fake = [
        r
        for r in rows
        if r["label"] == 0
    ]

    expected_total = (
        INDICVOICES_TARGET
        + MLAAD_TARGET
        + ASVSPOOF_REAL_TARGET
        + ASVSPOOF_FAKE_TARGET
    )

    print(f"Total rows: {len(rows):,}")
    print(f"REAL:       {len(real):,}")
    print(f"FAKE:       {len(fake):,}")

    print()
    print("By source:")

    source_counts = defaultdict(int)

    for row in rows:
        source_counts[row["source"]] += 1

    for source, count in sorted(source_counts.items()):
        print(f"  {source}: {count:,}")

    if len(rows) != expected_total:
        raise RuntimeError(
            f"Expected {expected_total:,} total rows "
            f"but got {len(rows):,}"
        )

    expected_sources = {
        "indicvoices": INDICVOICES_TARGET,
        "mlaad": MLAAD_TARGET,
        "asvspoof5": (
            ASVSPOOF_REAL_TARGET
            + ASVSPOOF_FAKE_TARGET
        ),
    }

    for source, expected in expected_sources.items():

        actual = source_counts[source]

        if actual != expected:
            raise RuntimeError(
                f"{source}: expected {expected:,}, "
                f"got {actual:,}"
            )

    # Verify paths.
    missing = []

    for row in rows:

        if not Path(row["path"]).exists():
            missing.append(row["path"])

            if len(missing) >= 20:
                break

    if missing:

        print()
        print("Missing files:")

        for path in missing:
            print(path)

        raise RuntimeError(
            f"{len(missing)} or more manifest files are missing."
        )

    print()
    print("PATH CHECK: PASSED")
    print("COUNT CHECK: PASSED")


# ---------------------------------------------------------------------
# Write manifest
# ---------------------------------------------------------------------

def write_manifest(rows, output_path: Path):

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "path",
        "label",
        "source",
        "language",
        "speaker",
        "split",
    ]

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    print()
    print("=" * 70)
    print("MANIFEST CREATED")
    print("=" * 70)

    print(f"File: {output_path.resolve()}")
    print(f"Rows: {len(rows):,}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Prepare the 140,000-clip Forensics 0.3B "
            "fine-tuning dataset."
        )
    )

    parser.add_argument(
        "--asv-root",
        required=True,
        help="ASVspoof 5 root directory.",
    )

    parser.add_argument(
        "--indicvoices-root",
        required=True,
        help="IndicVoices Parquet root directory.",
    )

    parser.add_argument(
        "--mlaad-root",
        default="training/forensics_data/mlaad",
        help="MLAAD audio directory.",
    )

    parser.add_argument(
        "--indicvoices-output",
        default="training/forensics_data/indicvoices",
        help=(
            "Where selected IndicVoices FLAC files "
            "will be extracted."
        ),
    )

    parser.add_argument(
        "--out",
        default="training/forensics_manifest.csv",
        help="Output CSV manifest.",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed.",
    )

    args = parser.parse_args()

    asv_root = Path(args.asv_root)
    indicvoices_root = Path(args.indicvoices_root)
    mlaad_root = Path(args.mlaad_root)
    indicvoices_output = Path(args.indicvoices_output)
    output_manifest = Path(args.out)

    print()
    print("=" * 70)
    print("FORENSICS 0.3B DATASET PREPARATION")
    print("=" * 70)

    print()
    print("TARGET DATASET:")
    print(f"  IndicVoices REAL : {INDICVOICES_TARGET:,}")
    print(f"  ASVspoof REAL    : {ASVSPOOF_REAL_TARGET:,}")
    print(f"  MLAAD FAKE       : {MLAAD_TARGET:,}")
    print(f"  ASVspoof FAKE    : {ASVSPOOF_FAKE_TARGET:,}")
    print(
        f"  TOTAL            : "
        f"{INDICVOICES_TARGET + MLAAD_TARGET + ASVSPOOF_REAL_TARGET + ASVSPOOF_FAKE_TARGET:,}"
    )

    # ---------------------------------------------------------------
    # 1. IndicVoices
    # ---------------------------------------------------------------

    iv_metadata = collect_indicvoices_metadata(
        indicvoices_root
    )

    iv_selected = select_indicvoices(
        iv_metadata,
        INDICVOICES_TARGET,
        args.seed,
    )

    iv_rows = extract_selected_indicvoices(
        iv_selected,
        indicvoices_output,
    )

    # ---------------------------------------------------------------
    # 2. MLAAD
    # ---------------------------------------------------------------

    mlaad_rows = read_mlaad(
        mlaad_root,
        MLAAD_TARGET,
        args.seed + 1,
    )

    # ---------------------------------------------------------------
    # 3. ASVspoof 5
    # ---------------------------------------------------------------

    asv_rows = read_asvspoof(
        asv_root
    )

    asv_real = select_asvspoof(
        asv_rows,
        label=1,
        target=ASVSPOOF_REAL_TARGET,
        seed=args.seed + 2,
    )

    asv_fake = select_asvspoof(
        asv_rows,
        label=0,
        target=ASVSPOOF_FAKE_TARGET,
        seed=args.seed + 3,
    )

    # ---------------------------------------------------------------
    # 4. Combine
    # ---------------------------------------------------------------

    rows = (
        iv_rows
        + mlaad_rows
        + asv_real
        + asv_fake
    )

    # ---------------------------------------------------------------
    # 5. Assign train/validation split
    # ---------------------------------------------------------------

    rows = assign_splits(
        rows,
        seed=args.seed + 4,
        val_fraction=0.10,
    )

    # ---------------------------------------------------------------
    # 6. Shuffle final manifest
    # ---------------------------------------------------------------

    rng = random.Random(args.seed + 5)

    rng.shuffle(rows)

    # ---------------------------------------------------------------
    # 7. Verify everything
    # ---------------------------------------------------------------

    verify_rows(rows)

    # ---------------------------------------------------------------
    # 8. Write CSV
    # ---------------------------------------------------------------

    write_manifest(
        rows,
        output_manifest,
    )

    print()
    print("=" * 70)
    print("SUCCESS")
    print("=" * 70)

    print(
        "The exact 140,000-clip dataset is ready "
        "for the next verification step."
    )


if __name__ == "__main__":
    main()