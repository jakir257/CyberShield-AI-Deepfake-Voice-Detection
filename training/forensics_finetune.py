"""
Fine-tune Forensics 0.3B (WavLM-large + AASIST) on a CSV manifest.

The base checkpoint remains untouched.

Each completed epoch is written under the selected output directory.
The active.safetensors file is replaced only after validation succeeds.
"""



from __future__ import annotations

import os

from django.db.migrations import loader

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import argparse
import csv
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn as nn
from safetensors.torch import load_file, save_file
from torch.utils.data import Dataset, DataLoader
from scipy.signal import resample_poly


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DETECTION = ROOT / "Deepfake Voice Detection" / "detection"

FOR_DIR = DETECTION / "media" / "forensics"

# Original pretrained Forensics 0.3B checkpoint.
BASE_WEIGHTS = FOR_DIR / "checkpoint_epoch_5.safetensors"
# This file is NEVER overwritten.
ACTIVE_WEIGHTS = FOR_DIR / "checkpoints" / "active.safetensors"

# Use the already fine-tuned model if it exists.
# Otherwise, use the original pretrained checkpoint.
if ACTIVE_WEIGHTS.is_file():
    START_WEIGHTS = ACTIVE_WEIGHTS
    print(f"Continuing training from: {START_WEIGHTS}")
else:
    START_WEIGHTS = BASE_WEIGHTS
    print(f"Starting first training from: {START_WEIGHTS}")

# Default location for real fine-tuning checkpoints.
OUT_DIR = FOR_DIR / "checkpoints"


# ============================================================
# AUDIO SETTINGS
# ============================================================

SR = 16000
SECONDS = 5
NSAMPLES = SR * SECONDS


# ============================================================
# AUDIO LOADING
# ============================================================

def load_audio(path):
    """
    Load audio as mono float32 at 16 kHz.
    Resample when necessary.
    """

    x, sr = sf.read(
        path,
        dtype="float32",
        always_2d=False
    )

    # Convert stereo/multi-channel audio to mono.
    if x.ndim > 1:
        x = x.mean(axis=1)

    # Resample to 16 kHz if necessary.
    if sr != SR:
        g = math.gcd(int(sr), SR)

        x = resample_poly(
            x,
            SR // g,
            int(sr) // g
        ).astype(np.float32)

    # Remove NaN / infinity.
    x = np.nan_to_num(x).astype(np.float32)

    if x.size == 0:
        raise ValueError(f"Empty audio: {path}")

    return x


# ============================================================
# DATASET
# ============================================================

class ManifestDataset(Dataset):

    def __init__(self, manifest, split, train=True):

        rows = []

        with open(
            manifest,
            "r",
            encoding="utf-8",
            newline=""
        ) as f:

            for r in csv.DictReader(f):

                if r.get("split") == split:
                    rows.append(r)

        if not rows:
            raise RuntimeError(
                f"No rows for split={split}"
            )

        self.rows = rows
        self.train = train

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):

        r = self.rows[i]

        x = load_audio(r["path"])

        n = NSAMPLES

        # ----------------------------------------------------
        # 5-second crop
        # ----------------------------------------------------

        if len(x) >= n:

            if self.train:
                # Random crop during training.
                start = random.randint(
                    0,
                    len(x) - n
                )

            else:
                # Deterministic center crop during validation.
                start = (len(x) - n) // 2

            x = x[start:start + n]

        else:

            # Repeat short audio until it reaches 5 seconds.
            reps = (n + len(x) - 1) // len(x)

            x = np.tile(
                x,
                reps
            )[:n]

        # ----------------------------------------------------
        # Peak normalization
        # ----------------------------------------------------

        peak = float(
            np.max(np.abs(x))
        )

        if peak > 1e-8:
            x = x / peak

        return (
            torch.from_numpy(
                np.ascontiguousarray(x)
            ),
            torch.tensor(
                float(r["label"])
            )
        )


# ============================================================
# CHECKPOINT SAVING
# ============================================================

def atomic_save(model, path):

    path = Path(path)

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tensors = {
        k: v.detach().cpu().contiguous()
        for k, v in model.state_dict().items()
    }

    save_file(
        tensors,
        str(tmp)
    )

    os.replace(
        tmp,
        path
    )


# ============================================================
# VALIDATION
# ============================================================

def evaluate(model, loader, device):

    model.eval()

    loss_fn = nn.BCEWithLogitsLoss()

    total_loss = 0.0

    ys = []
    ps = []

    with torch.no_grad():

        for x, y in loader:

            x = x.to(
                device,
                non_blocking=True
            )

            y = y.to(device, non_blocking=True)

            logits = model(x)

            loss = loss_fn(
                logits,
                y
            )

            total_loss += (
                float(loss.item()) * len(y)
            )

            ys.append(
                y.cpu().numpy()
            )

            ps.append(
                torch.sigmoid(logits)
                .cpu()
                .numpy()
            )

    y = np.concatenate(ys)

    p = np.concatenate(ps)

    # --------------------------------------------------------
    # EER and accuracy
    # --------------------------------------------------------

    try:

        from sklearn.metrics import (
            roc_curve,
            accuracy_score
        )

        fpr, tpr, thr = roc_curve(
            y,
            p
        )

        fnr = 1 - tpr

        j = int(
            np.nanargmin(
                np.abs(fpr - fnr)
            )
        )

        eer = float(
            (fpr[j] + fnr[j]) / 2
        )

        cutoff = float(
            thr[j]
        )

        acc = float(
            accuracy_score(
                y,
                p >= 0.5
            )
        )

    except Exception:

        eer = float("nan")

        cutoff = 0.5

        acc = float(
            ((p >= 0.5) == y).mean()
        )

    return (
        total_loss / len(y),
        eer,
        cutoff,
        acc
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # COMMAND-LINE ARGUMENTS
    # --------------------------------------------------------

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--manifest",
        default=str(
            ROOT
            / "training"
            / "forensics_manifest.csv"
        )
    )

    ap.add_argument(
        "--epochs",
        type=int,
        default=3
    )

    ap.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Actual batch size; increase only if GPU memory allows."
    )

    ap.add_argument(
        "--grad-accum",
        type=int,
        default=2
    )

    ap.add_argument(
        "--lr",
        type=float,
        default=5e-6
    )

    ap.add_argument(
        "--weight-decay",
        type=float,
        default=1e-4
    )

    ap.add_argument(
        "--workers",
        type=int,
        default=0 if os.name == "nt" else 6,
        help="DataLoader workers. Use 0 if Windows multiprocessing causes issues."
    )

    ap.add_argument(
        "--prefetch",
        type=int,
        default=4,
        help="Batches prefetched per worker."
    )

    ap.add_argument(
        "--empty-cache",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Call torch.cuda.empty_cache() after each epoch. Usually leave disabled for speed."
    )

    # ap.add_argument(
    #     "--limit",
    #     type=int,
    #     default=None,
    #     help=(
    #         "Limit rows per class for a smoke test. "
    #         "For example --limit 1 gives 1 real + 1 fake "
    #         "per split."
    #     )
    # )

    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Number of real and fake clips to use per split."
        )
    )

    ap.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Starting position within the deterministically shuffled "
            "real/fake lists. Use with --limit to select a non-overlapping "
            "subset."
        )
    )

    ap.add_argument(
        "--freeze-wavlm",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Freeze WavLM by default. Use --no-freeze-wavlm for full fine-tuning."
    )

    ap.add_argument(
        "--grad-clip",
        type=float,
        default=1.0,
        help="Maximum gradient norm."
    )

    # IMPORTANT:
    # Allows smoke tests to save somewhere separate from
    # the real active.safetensors.
    ap.add_argument(
        "--output-dir",
        default=str(OUT_DIR),
        help=(
            "Directory for epoch checkpoints, metrics, "
            "and active.safetensors."
        )
    )

    args = ap.parse_args()

    # --------------------------------------------------------
    # RESOLVE OUTPUT DIRECTORY
    # --------------------------------------------------------

    output_dir = Path(
        args.output_dir
    )

    # --------------------------------------------------------
    # CHECK REQUIRED FILES
    # --------------------------------------------------------

    if not BASE_WEIGHTS.is_file():

        raise SystemExit(
            f"Missing base checkpoint:\n{BASE_WEIGHTS}"
        )

    if not Path(args.manifest).is_file():

        raise SystemExit(
            f"Missing manifest:\n{args.manifest}"
        )

    # --------------------------------------------------------
    # DEVICE
    # --------------------------------------------------------

    device = torch.device(
        "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )

    print(
        "DEVICE",
        device,
        flush=True
    )

    if device.type == "cuda":

        print(
            "GPU",
            torch.cuda.get_device_name(0),
            flush=True
        )

    # # --------------------------------------------------------
    # # DATASETS
    # # --------------------------------------------------------

    # train_ds = ManifestDataset(
    #     args.manifest,
    #     "train",
    #     True
    # )

    # val_ds = ManifestDataset(
    #     args.manifest,
    #     "val",
    #     False
    # )

    # --------------------------------------------------------
    # DATASETS
    # --------------------------------------------------------

    print("CREATING TRAIN DATASET", flush=True)

    train_ds = ManifestDataset(
        args.manifest,
        "train",
        True
    )

    print(
        f"TRAIN DATASET LOADED: {len(train_ds)}",
        flush=True
    )

    print("CREATING VAL DATASET", flush=True)

    val_ds = ManifestDataset(
        args.manifest,
        "val",
        False
    )

    print(
        f"VAL DATASET LOADED: {len(val_ds)}",
        flush=True
)

    # # --------------------------------------------------------
    # # OPTIONAL SMOKE TEST LIMIT
    # # --------------------------------------------------------

    # if args.limit:

    #     def trim(ds):

    #         rows = ds.rows

    #         real = [
    #             r
    #             for r in rows
    #             if r["label"] == "1"
    #         ]

    #         fake = [
    #             r
    #             for r in rows
    #             if r["label"] == "0"
    #         ]

    #         # Deterministic selection.
    #         random.Random(42).shuffle(real)
    #         random.Random(43).shuffle(fake)

    #         ds.rows = (
    #             real[:args.limit]
    #             + fake[:args.limit]
    #         )

    #         random.shuffle(ds.rows)

    #     trim(train_ds)
    #     trim(val_ds)

    # print(
    #     f"TRAIN {len(train_ds)}  VAL {len(val_ds)}",
    #     flush=True
    # )

    # --------------------------------------------------------
    # OPTIONAL DATA SUBSET SELECTION
    # --------------------------------------------------------

    if args.limit is not None:

        def trim(ds):

            rows = ds.rows

            real = [
                r
                for r in rows
                if r["label"] == "1"
            ]

            fake = [
                r
                for r in rows
                if r["label"] == "0"
            ]

            # ------------------------------------------------
            # IMPORTANT:
            # Always create the same global ordering.
            # This allows offset + limit to select
            # non-overlapping portions across runs.
            # ------------------------------------------------

            random.Random(42).shuffle(real)
            random.Random(43).shuffle(fake)

            start = args.offset
            end = start + args.limit

            selected_real = real[start:end]
            selected_fake = fake[start:end]

            if len(selected_real) < args.limit:
                raise RuntimeError(
                    f"Not enough REAL clips for offset={start}, "
                    f"limit={args.limit}. "
                    f"Available REAL clips: {len(real)}"
                )

            if len(selected_fake) < args.limit:
                raise RuntimeError(
                    f"Not enough FAKE clips for offset={start}, "
                    f"limit={args.limit}. "
                    f"Available FAKE clips: {len(fake)}"
                )

            ds.rows = (
                selected_real
                + selected_fake
            )

            # Shuffle the selected training examples.
            random.shuffle(ds.rows)

        trim(train_ds)
        # trim(val_ds)

    # --------------------------------------------------------
    # LOAD EXACT FORENSICS 0.3B ARCHITECTURE
    # --------------------------------------------------------

    sys.path.insert(
        0,
        str(FOR_DIR)
    )

    from model import DeepfakeDetector

    model = DeepfakeDetector().to(device)

    # Load the original pretrained checkpoint.
    model.load_state_dict(
        load_file(
            str(START_WEIGHTS)
        ),
        strict=False
    )

    # print(
    #     "BASE_CHECKPOINT_LOADED",
    #     BASE_WEIGHTS,
    #     flush=True
    # )
    print(
        "START_CHECKPOINT_LOADED",
        START_WEIGHTS,
        flush=True
    )

    # --------------------------------------------------------
    # OPTIONAL WAVLM FREEZING
    # --------------------------------------------------------

    if args.freeze_wavlm:

        print(
            "WAVLM_FROZEN",
            flush=True
        )

        for p in model.wavlm.parameters():
            p.requires_grad = False

    else:

        print(
            "WAVLM_TRAINABLE",
            flush=True
        )

    model.train()
    if args.freeze_wavlm:
        model.wavlm.eval()
        print("TRAINING_MODE AASIST_HEAD_ONLY", flush=True)
    else:
        print("TRAINING_MODE FULL_WAVLM_FINE_TUNE", flush=True)

    # --------------------------------------------------------
    # GRADIENT CHECKPOINTING
    # --------------------------------------------------------

    if (
        hasattr(
            model.wavlm,
            "gradient_checkpointing_enable"
        )
        and not args.freeze_wavlm
    ):

        try:

            model.wavlm.gradient_checkpointing_enable()

            print(
                "WAVLM_GRADIENT_CHECKPOINTING_ENABLED",
                flush=True
            )

        except Exception as e:

            print(
                "WARNING: Could not enable gradient checkpointing:",
                e,
                flush=True
            )

    # --------------------------------------------------------
    # DATA LOADERS
    # --------------------------------------------------------

    loader_kwargs = {
        "batch_size": args.batch_size,
        "num_workers": args.workers,
        "pin_memory": device.type == "cuda",
        "drop_last": False,
    }
    if args.workers > 0:
        loader_kwargs.update(persistent_workers=True, prefetch_factor=args.prefetch)

    loader = DataLoader(train_ds, shuffle=True, **loader_kwargs)

    vloader_kwargs = {
        "batch_size": args.batch_size,
        "num_workers": args.workers,
        "pin_memory": device.type == "cuda",
    }
    if args.workers > 0:
        vloader_kwargs.update(persistent_workers=True, prefetch_factor=args.prefetch)

    vloader = DataLoader(val_ds, shuffle=False, **vloader_kwargs)

    # --------------------------------------------------------
    # OPTIMIZER
    # --------------------------------------------------------

    trainable_parameters = [
        p
        for p in model.parameters()
        if p.requires_grad
    ]

    opt = torch.optim.AdamW(
        trainable_parameters,
        lr=args.lr,
        weight_decay=args.weight_decay
    )

    # --------------------------------------------------------
    # LEARNING-RATE SCHEDULER
    # --------------------------------------------------------

    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt,
        T_max=max(
            1,
            args.epochs
        )
    )

    # --------------------------------------------------------
    # LOSS
    # --------------------------------------------------------

    loss_fn = nn.BCEWithLogitsLoss()

    # --------------------------------------------------------
    # AMP
    # --------------------------------------------------------

    amp = device.type == "cuda"
    if amp and torch.cuda.is_bf16_supported():
        amp_dtype = torch.bfloat16
        scaler_enabled = False
        print("AMP_DTYPE bfloat16", flush=True)
    elif amp:
        amp_dtype = torch.float16
        scaler_enabled = True
        print("AMP_DTYPE float16", flush=True)
    else:
        amp_dtype = torch.float32
        scaler_enabled = False
        print("AMP_DTYPE disabled", flush=True)

    scaler = torch.amp.GradScaler("cuda", enabled=scaler_enabled)
    print("EFFECTIVE_BATCH", args.batch_size * args.grad_accum, flush=True)

    # --------------------------------------------------------
    # OUTPUT DIRECTORY
    # --------------------------------------------------------

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        "OUTPUT_DIR",
        output_dir,
        flush=True
    )

    # --------------------------------------------------------
    # SAVE EXACT TRAINING FILE LIST
    # --------------------------------------------------------

    if args.limit is not None:

        selection_file = (
            output_dir
            / f"selected_train_offset_{args.offset}_limit_{args.limit}.txt"
        )

        selection_file.write_text(
            "\n".join(
                r["path"]
                for r in train_ds.rows
            ),
            encoding="utf-8"
        )

        print(
            "TRAINING_SELECTION_FILE",
            selection_file,
            flush=True
        )

        print(
            f"SELECTED_TRAIN_REAL "
            f"{sum(r['label'] == '1' for r in train_ds.rows)}",
            flush=True
        )

        print(
            f"SELECTED_TRAIN_FAKE "
            f"{sum(r['label'] == '0' for r in train_ds.rows)}",
            flush=True
        )


    # --------------------------------------------------------
    # TRAINING
    # --------------------------------------------------------

    # best = float("inf")


    # best = float("inf")

    # --------------------------------------------------------
    # LOAD GLOBAL BEST VALIDATION LOSS
    # --------------------------------------------------------

    best = float("inf")

    active_metrics_file = output_dir / "active_metrics.json"

    if active_metrics_file.is_file():
        try:
            previous_metrics = json.loads(
                active_metrics_file.read_text(encoding="utf-8")
            )

            previous_best = float(
                previous_metrics.get("val_loss", float("inf"))
            )

            if math.isfinite(previous_best):
                best = previous_best

            print(
                f"PREVIOUS_BEST_VAL_LOSS {best:.6f}",
                flush=True
            )

        except Exception as e:
            print(
                f"WARNING: could not read active_metrics.json: {e}",
                flush=True
            )

    print(
        f"GLOBAL_BEST_VAL_LOSS {best:.6f}",
        flush=True
    )

    # # --------------------------------------------------------
    # # GLOBAL EPOCH NUMBER
    # # --------------------------------------------------------

    # existing_epochs = []

    # for p in output_dir.glob(
    #     "forensics_indic_asv5_mlaad_epoch_*.safetensors"
    # ):
    #     try:
    #         n = int(
    #             p.stem.split("_")[-1]
    #         )
    #         existing_epochs.append(n)
    #     except ValueError:
    #         pass

    # global_epoch_start = (
    #     max(existing_epochs)
    #     if existing_epochs
    #     else 0
    # )

    # for epoch in range(
    #     1,
    #     args.epochs + 1
    # ):

    #     global_epoch = (
    #         global_epoch_start
    #         + epoch
    #     )

    for epoch in range(
        1,
        args.epochs + 1
    ):
        model.train()
        if args.freeze_wavlm:
            model.wavlm.eval()

        opt.zero_grad(
            set_to_none=True
        )

        running = 0.0

        t0 = time.time()

        optimizer_updated = False

        # ----------------------------------------------------
        # TRAINING BATCHES
        # ----------------------------------------------------

        for step, (x, y) in enumerate(
            loader,
            1
        ):

            x = x.to(
                device,
                non_blocking=True
            )

            y = y.to(device, non_blocking=True)

            # ------------------------------------------------
            # Mixed precision
            # ------------------------------------------------

            with torch.amp.autocast(
                "cuda",
                dtype=amp_dtype,
                enabled=amp
            ):

                logits = model(x)

                loss = (
                    loss_fn(
                        logits,
                        y
                    )
                    / args.grad_accum
                )

            if not torch.isfinite(loss).all():
                print(
                    f"WARNING: non-finite loss at epoch={epoch} step={step}; skipping batch.",
                    flush=True
                )
                opt.zero_grad(set_to_none=True)
                continue

            # ------------------------------------------------
            # Backpropagation
            # ------------------------------------------------

            scaler.scale(loss).backward()

            # ------------------------------------------------
            # Gradient accumulation
            # ------------------------------------------------

            if (
                step % args.grad_accum == 0
                or step == len(loader)
            ):

                scaler.unscale_(opt)

                grads_finite = all(
                    p.grad is None or torch.isfinite(p.grad).all()
                    for p in trainable_parameters
                )
                if not grads_finite:
                    print(
                        f"WARNING: non-finite gradients at epoch={epoch} step={step}; skipping update.",
                        flush=True
                    )
                    opt.zero_grad(set_to_none=True)
                    if scaler_enabled:
                        scaler.update()
                    continue

                torch.nn.utils.clip_grad_norm_(
                    trainable_parameters,
                    args.grad_clip
                )

                old_scale = scaler.get_scale()

                scaler.step(opt)

                scaler.update()

                new_scale = scaler.get_scale()

                if new_scale >= old_scale:
                    optimizer_updated = True

                opt.zero_grad(
                    set_to_none=True
                )

            running += (
                float(loss.item())
                * args.grad_accum
            )

            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            if (
                step % 50 == 0
                or step == len(loader)
            ):

                pct = (
                    step
                    / len(loader)
                    * 100
                )

                print(
                    f"PROGRESS "
                    f"{epoch} "
                    f"{step} "
                    f"{len(loader)} "
                    f"{pct:.1f}% "
                    f"loss={running / step:.5f}",
                    flush=True
                )

        # ----------------------------------------------------
        # UPDATE LR
        # ----------------------------------------------------

        # Step the epoch-level scheduler only when at least
        # one optimizer update actually occurred.
        if optimizer_updated:
            sched.step()

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        print("STARTING VALIDATION", flush=True)

        val_loss, eer, cutoff, acc = evaluate(
            model,
            vloader,
            device
        )

        print("VALIDATION FINISHED", flush=True)

        epoch_time = time.time() - t0

        train_loss = (
            running
            / len(loader)
        )

        print(
            f"EPOCH {epoch} "
            f"train_loss={train_loss:.6f} "
            f"val_loss={val_loss:.6f} "
            f"eer={eer * 100:.3f} "
            f"cutoff={cutoff:.5f} "
            f"acc={acc * 100:.2f} "
            f"time={epoch_time / 60:.2f}min "
            f"train_clips_per_sec={len(train_ds) / max(epoch_time, 1e-6):.2f}",
            flush=True
        )

        # ----------------------------------------------------
        # SAVE EPOCH CHECKPOINT
        # ----------------------------------------------------

        print("STARTING CHECKPOINT SAVE", flush=True)

        ck = (
            output_dir
            / f"forensics_indic_asv5_mlaad_epoch_{epoch}.safetensors"
        )

        atomic_save(
            model,
            ck
        )

        print("EPOCH CHECKPOINT SAVED", flush=True)

        # ----------------------------------------------------
        # SAVE METRICS
        # ----------------------------------------------------

        metrics = {
            "epoch": epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "eer": eer,
            "threshold": cutoff,
            "accuracy": acc,
            "learning_rate": opt.param_groups[0]["lr"],
            "trained_at": time.time(),
            "base_checkpoint": str(BASE_WEIGHTS),
            "manifest": str(args.manifest),
        }

        (
            output_dir
            / f"epoch_{epoch}_metrics.json"
        ).write_text(
            json.dumps(
                metrics,
                indent=2
            ),
            encoding="utf-8"
        )

        # ----------------------------------------------------
        # UPDATE ACTIVE CHECKPOINT
        # ----------------------------------------------------

        if (
            math.isfinite(val_loss)
            and math.isfinite(eer)
            and math.isfinite(acc)
            and val_loss < best        # keep this uncommented if you want to save only the best checkpoint
        ):

            best = val_loss

            atomic_save(
                model,
                output_dir / "active.safetensors"
            )

            (
                output_dir
                / "active_metrics.json"
            ).write_text(
                json.dumps(
                    metrics,
                    indent=2
                ),
                encoding="utf-8"
            )

            print(
                "ACTIVE_CHECKPOINT",
                output_dir / "active.safetensors",
                flush=True
            )

        # ----------------------------------------------------
        # GPU MEMORY CLEANUP
        # ----------------------------------------------------

        if device.type == "cuda":

            torch.cuda.empty_cache()

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    print(
        "TRAINING_DONE",
        flush=True
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()