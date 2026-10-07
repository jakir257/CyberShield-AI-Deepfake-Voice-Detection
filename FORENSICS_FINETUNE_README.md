# Forensics 0.3B fine-tuning patch

This replaces the Train Model tab's old sklearn FoR/ASVspoof-2019 trainer with
fine-tuning of the existing Forensics 0.3B checkpoint.

Data:
- REAL: IndicVoices + ASVspoof 5 bonafide
- FAKE: MLAAD + ASVspoof 5 spoof
- target: 70k real / 70k fake (the manifest builder defaults to this)

## 1. Install

From `Deepfake Voice Detection/detection`:

    pip install datasets soundfile huggingface_hub safetensors scipy scikit-learn
    hf auth login

Accept the IndicVoices and MLAAD dataset terms on Hugging Face first.

## 2. Curate HF audio

From the project root (`Deepfake Voice Detection`):

    python training/download_forensics_data.py --out training/forensics_data --real 50000 --fake 50000

This streams selected clips rather than downloading the complete releases.

## 3. ASVspoof 5

Extract the five training archives into one directory:

    D:\Datasets\ASVspoof5\flac_T\
    D:\Datasets\ASVspoof5\ASVspoof5.train.tsv

You already have `flac_T_aa.tar` and `flac_T_ae.tar` etc.; do not use the old
ASVspoof-2019 preparation script.

## 4. Build the manifest

    python training/prepare_forensics_data.py --asv-root "D:\Datasets\ASVspoof5" --real-target 70000 --fake-target 70000

For a smoke test:

    python training/prepare_forensics_data.py --asv-root "D:\Datasets\ASVspoof5" --real-target 1000 --fake-target 1000

## 5. Train from the existing checkpoint

Smoke test first:

    python training/forensics_finetune.py --limit 500 --epochs 1

Then the real run:

    python training/forensics_finetune.py --epochs 3 --batch-size 1 --grad-accum 8 --lr 1e-5

The original `media/forensics/checkpoint_epoch_5.safetensors` is never overwritten.
The best validation model becomes:

    media/forensics/checkpoints/active.safetensors

The Django app automatically switches to that checkpoint after the run.
