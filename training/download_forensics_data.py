"""
Download a curated 50,000-clip MLAAD subset for Forensics 0.3B fine-tuning.

IndicVoices is intentionally NOT downloaded here because the required
IndicVoices clips have already been collected separately.

MLAAD target distribution:

    English    10,000
    Hindi       8,000
    German      5,000
    French      5,000
    Spanish     5,000
    Italian     5,000
    Polish      4,000
    Russian     4,000
    Ukrainian   4,000
    -----------------
    Total      50,000

Requirements:

    pip install huggingface_hub

Authentication:

    hf auth login

The MLAAD dataset is gated. Your Hugging Face account must have
accepted the MLAAD dataset access conditions.
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download, hf_hub_download

from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

DATASET = "mueller91/MLAAD"

DEFAULT_OUTPUT = Path("training/forensics_data/mlaad")

# Keep the agreed 50,000 fake-clip distribution.
LANGUAGE_TARGETS = {
    "en": 10_000,
    "hi": 8_000,
    "de": 5_000,
    "fr": 5_000,
    "es": 5_000,
    "it": 5_000,
    "pl": 4_000,
    "ru": 4_000,
    "uk": 4_000,
}

TOTAL_TARGET = sum(LANGUAGE_TARGETS.values())

# Fixed seed means the same files are selected if the script is rerun.
RANDOM_SEED = 42


# ---------------------------------------------------------------------
# FIND MLAAD WAV FILES
# ---------------------------------------------------------------------

def get_language_files(api: HfApi, language: str):
    """
    Get all WAV files belonging to:

        fake/<language>/<model>/*.wav

    This only reads the repository file listing.
    It does NOT download the audio.
    """

    base = f"fake/{language}"

    print()
    print("=" * 70)
    print(f"Scanning MLAAD: {base}")
    print("=" * 70)

    files_by_model = defaultdict(list)

    try:
        items = api.list_repo_tree(
            repo_id=DATASET,
            repo_type="dataset",
            path_in_repo=base,
            recursive=True,
        )

        for item in items:
            path = getattr(item, "rfilename", None)

            if not path:
                continue

            if not path.lower().endswith(".wav"):
                continue

            # Expected:
            #
            # fake/en/ModelName/file.wav
            #
            relative = path[len(base):].lstrip("/")

            parts = relative.split("/")

            if len(parts) < 2:
                continue

            # Everything before the filename is considered the model path.
            model = "/".join(parts[:-1])

            files_by_model[model].append(path)

    except Exception as exc:
        print()
        print(f"ERROR while reading MLAAD file list for '{language}':")
        print(exc)
        return {}

    total_files = sum(len(v) for v in files_by_model.values())

    print(f"Models found : {len(files_by_model)}")
    print(f"WAV files    : {total_files}")

    for model in sorted(files_by_model):
        print(f"  {model}: {len(files_by_model[model]):,}")

    return dict(files_by_model)


# ---------------------------------------------------------------------
# SELECT FILES
# ---------------------------------------------------------------------

def select_files(files_by_model, target, language):
    """
    Select approximately 'target' WAV files while spreading the
    selection across as many TTS model folders as possible.
    """

    if not files_by_model:
        return []

    rng = random.Random(RANDOM_SEED)

    # Shuffle files inside every model so we do not always take the
    # alphabetically first files.
    model_files = {}

    for model, files in files_by_model.items():
        files_copy = list(files)
        rng.shuffle(files_copy)
        model_files[model] = files_copy

    models = sorted(model_files)

    # Remove empty models.
    models = [
        model
        for model in models
        if model_files[model]
    ]

    selected = []

    # Round-robin selection:
    #
    # model 1 -> one clip
    # model 2 -> one clip
    # model 3 -> one clip
    # ...
    #
    # then repeat.
    #
    # This prevents the dataset from being dominated by one TTS system.

    positions = {
        model: 0
        for model in models
    }

    while len(selected) < target:

        added_this_round = False

        for model in models:

            if len(selected) >= target:
                break

            pos = positions[model]
            files = model_files[model]

            if pos >= len(files):
                continue

            selected.append(files[pos])
            positions[model] += 1
            added_this_round = True

        if not added_this_round:
            break

    print()
    print(f"{language}: selected {len(selected):,} / {target:,}")

    # Print model distribution.
    selected_by_model = defaultdict(int)

    for path in selected:
        relative = path[len(f"fake/{language}/"):]
        model = "/".join(relative.split("/")[:-1])
        selected_by_model[model] += 1

    print("Selected model distribution:")

    for model, count in sorted(selected_by_model.items()):
        print(f"  {model}: {count:,}")

    return selected


# ---------------------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------------------

def download_selected(selected_files, output_dir, max_workers=32):
    """
    Download MLAAD files concurrently.

    Uses the Hugging Face cache for each download and then copies the
    completed file into the final MLAAD directory.

    Existing files are skipped.
    Failed files are retried.
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(selected_files)

    pending = []
    skipped = 0

    for repo_file in selected_files:
        local_path = output_dir / repo_file

        if local_path.exists() and local_path.stat().st_size > 0:
            skipped += 1
        else:
            pending.append(repo_file)

    print()
    print("=" * 70)
    print("MLAAD PARALLEL DOWNLOAD")
    print("=" * 70)
    print(f"Total selected : {total}")
    print(f"Already present: {skipped}")
    print(f"To download    : {len(pending)}")
    print(f"Workers        : {max_workers}")
    print("=" * 70)
    print()

    if not pending:
        print("All selected files are already downloaded.")
        return skipped, 0

    successful = skipped
    failed_files = []

    start_time = time.time()

    # ---------------------------------------------------------
    # Download ONE file
    # ---------------------------------------------------------
    def download_one(repo_file):

        final_path = output_dir / repo_file

        # Already downloaded
        if final_path.exists() and final_path.stat().st_size > 0:
            return "skipped", repo_file, ""

        last_error = ""

        for attempt in range(1, 4):

            try:

                # -------------------------------------------------
                # IMPORTANT:
                # Do NOT use local_dir here.
                #
                # This prevents many concurrent downloads from
                # fighting over the same local_dir download cache.
                # -------------------------------------------------
                cached_file = hf_hub_download(
                    repo_id=DATASET,
                    repo_type="dataset",
                    filename=repo_file,
                )

                cached_file = Path(cached_file)

                if not cached_file.exists():
                    raise FileNotFoundError(
                        f"Hugging Face cache file not found: {cached_file}"
                    )

                # -------------------------------------------------
                # Create final directory
                # -------------------------------------------------
                final_path.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                # -------------------------------------------------
                # Copy completed file to our MLAAD directory
                # -------------------------------------------------
                import shutil

                temp_final = final_path.with_suffix(
                    final_path.suffix + ".tmp"
                )

                shutil.copy2(
                    cached_file,
                    temp_final
                )

                # Atomic rename after successful copy
                temp_final.replace(final_path)

                # Verify
                if (
                    final_path.exists()
                    and final_path.stat().st_size > 0
                ):
                    return "success", repo_file, ""

                last_error = "Final file verification failed"

            except Exception as exc:

                last_error = str(exc)

                if attempt < 3:
                    time.sleep(2 ** attempt)

        return "failed", repo_file, last_error

    # ---------------------------------------------------------
    # PARALLEL DOWNLOAD
    # ---------------------------------------------------------

    completed = 0

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        futures = {
            executor.submit(download_one, repo_file): repo_file
            for repo_file in pending
        }

        for future in as_completed(futures):

            status, repo_file, error = future.result()

            completed += 1

            if status == "success":
                successful += 1

            elif status == "failed":
                failed_files.append(
                    (repo_file, error)
                )

            elapsed = time.time() - start_time

            speed = (
                completed / elapsed
                if elapsed > 0
                else 0
            )

            remaining = len(pending) - completed

            eta_seconds = (
                remaining / speed
                if speed > 0
                else 0
            )

            eta_minutes = eta_seconds / 60

            if status == "success":

                print(
                    f"[{completed}/{len(pending)}] "
                    f"OK | "
                    f"{speed:.2f} files/s | "
                    f"ETA {eta_minutes:.1f} min | "
                    f"{repo_file}"
                )

            elif status == "failed":

                print(
                    f"[{completed}/{len(pending)}] "
                    f"FAILED | "
                    f"{repo_file}"
                )

                print(
                    f"    {error}"
                )

            else:

                print(
                    f"[{completed}/{len(pending)}] "
                    f"SKIP | "
                    f"{repo_file}"
                )

    # ---------------------------------------------------------
    # Save failed files
    # ---------------------------------------------------------

    if failed_files:

        failed_path = (
            output_dir / "failed_downloads.txt"
        )

        with open(
            failed_path,
            "w",
            encoding="utf-8"
        ) as f:

            for repo_file, error in failed_files:

                f.write(repo_file)
                f.write("\n")

        print()
        print("Failed files saved to:")
        print(f"  {failed_path}")

    # ---------------------------------------------------------
    # Final result
    # ---------------------------------------------------------

    elapsed_total = (
        time.time() - start_time
    )

    print()
    print("=" * 70)
    print("MLAAD DOWNLOAD RESULT")
    print("=" * 70)
    print(f"Successful : {successful}")
    print(f"Failed     : {len(failed_files)}")
    print(
        f"Elapsed    : "
        f"{elapsed_total / 60:.1f} minutes"
    )
    print("=" * 70)

    return successful, len(failed_files)


# ---------------------------------------------------------------------
# CHECK EXISTING DOWNLOAD
# ---------------------------------------------------------------------

def count_existing_wavs(output_dir):
    """
    Count WAV files already downloaded into the MLAAD directory.
    """

    if not output_dir.exists():
        return 0

    return sum(
        1
        for p in output_dir.rglob("*.wav")
        if p.is_file()
    )


# ---------------------------------------------------------------------
# MAIN MLAAD PROCESS
# ---------------------------------------------------------------------

def download_mlaad(output_dir, test_limit=None):

    print()
    print("=" * 70)
    print("MLAAD DOWNLOAD FOR FORENSICS 0.3B")
    print("=" * 70)

    print(f"Dataset : {DATASET}")
    print(f"Output  : {output_dir}")
    print(f"Target  : {TOTAL_TARGET:,} clips")

    existing = count_existing_wavs(output_dir)

    if existing:
        print()
        print(f"Existing MLAAD WAV files: {existing:,}")

    # -------------------------------------------------------------
    # Hugging Face API
    # -------------------------------------------------------------

    api = HfApi()

    all_selected = []

    # -------------------------------------------------------------
    # Process every language
    # -------------------------------------------------------------

    for language, target in LANGUAGE_TARGETS.items():

        if test_limit is not None:
            target = min(target, test_limit)

        files_by_model = get_language_files(
            api,
            language,
        )

        selected = select_files(
            files_by_model,
            target,
            language,
        )

        all_selected.extend(selected)

    # -------------------------------------------------------------
    # Final count
    # -------------------------------------------------------------

    print()
    print("=" * 70)
    print("FINAL MLAAD SELECTION")
    print("=" * 70)

    print(f"Selected files: {len(all_selected):,}")
    print(f"Expected      : {TOTAL_TARGET if test_limit is None else 'TEST MODE'}")

    if test_limit is None and len(all_selected) < TOTAL_TARGET:
        print()
        print(
            "WARNING: MLAAD did not contain enough WAV files "
            "for the requested distribution."
        )
        print(
            f"Requested: {TOTAL_TARGET:,}"
        )
        print(
            f"Available: {len(all_selected):,}"
        )

    # -------------------------------------------------------------
    # Test mode
    # -------------------------------------------------------------

    if test_limit is not None:

        print()
        print("=" * 70)
        print("TEST MODE")
        print("=" * 70)

        print(
            f"Only up to {test_limit} clips per language were selected."
        )

    # -------------------------------------------------------------
    # Download
    # -------------------------------------------------------------

    download_selected(
        all_selected,
        output_dir,
    )

    # -------------------------------------------------------------
    # Final verification
    # -------------------------------------------------------------

    final_count = count_existing_wavs(output_dir)

    print()
    print("=" * 70)
    print("DOWNLOAD COMPLETE")
    print("=" * 70)

    print(f"MLAAD WAV files currently present: {final_count:,}")
    print(f"Location: {output_dir.resolve()}")

    if test_limit is None:
        if final_count >= TOTAL_TARGET:
            print()
            print("SUCCESS: MLAAD 50,000-clip target reached.")
        else:
            print()
            print(
                "MLAAD download completed, but the local WAV count "
                "is below 50,000."
            )


# ---------------------------------------------------------------------
# ARGUMENTS
# ---------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Download selected MLAAD clips for Forensics fine-tuning."
    )

    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="MLAAD output directory.",
    )

    parser.add_argument(
        "--test",
        type=int,
        default=None,
        help=(
            "Test mode: download at most this many clips "
            "per language instead of the full 50,000."
        ),
    )

    args = parser.parse_args()

    output_dir = Path(args.out)

    download_mlaad(
        output_dir=output_dir,
        test_limit=args.test,
    )


if __name__ == "__main__":
    main()