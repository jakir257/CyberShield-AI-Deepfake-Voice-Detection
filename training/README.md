# Training a real-vs-fake voice detector

Everything here is a one-off experiment. Nothing in this folder runs as part of
the Django app. Datasets and `.pkl` models are gitignored — they are big.

## Datasets

| Dataset | Link | Size | Why |
|---|---|---|---|
| ASVspoof 2019 LA | <https://datashare.ed.ac.uk/handle/10283/3336> | ~23 GB | what we trained on; studio-clean, 2019 attacks |
| In-the-Wild | <https://huggingface.co/datasets/mueller91/In-The-Wild> | 8.2 GB | real-world conditions, 58 public figures |
| Fake-or-Real (FoR) | <https://bil.eecs.yorku.ca/datasets/> | ~10 GB | balanced, ships its own train/val/test splits |

Direct downloads, no signup on either:

```
# In-the-Wild (CC-BY-SA-4.0) - 31,779 clips, 19,963 real / 11,816 fake
wget https://huggingface.co/datasets/mueller91/In-The-Wild/resolve/main/release_in_the_wild.zip
```

**Why the other two matter here.** A model trained on 2019 LA alone scores ~11%
EER on 2019 LA and calls essentially *every* real phone recording fake — see
"the bandwidth trap" below. FoR and In-the-Wild contain the compressed,
real-world audio that 2019 LA does not: the LA model scores **81% EER** on FoR,
worse than guessing.

In-the-Wild is intended as a test set; if you train on it, split by speaker
(it has 58) or the score means nothing.

ASVspoof 2021 DF was dropped: its audio ships unlabelled and the labels are
hosted only on `asvspoof.org`, which is IPv4-only and unreachable from some
networks. FoR covers the same ground and needs no second download.

## Quick path — pick a dataset and train

If the data is already unpacked, skip the manual steps. This finds it, sorts
it, and trains:

```
cd training
python prepare_asvspoof.py --dataset               # what is on disk?
python prepare_asvspoof.py --dataset la --train    # 2019 LA
python prepare_asvspoof.py --dataset for --train   # Fake-or-Real
```

`--dataset` on its own prints a readiness table:

```
  la     READY  ASVspoof 2019 LA
         full-band studio audio; the bandwidth trap applies
         D:\...\LA

  for    READY  Fake-or-Real (FoR)
         balanced 26,941 per class; real is all wav, fake is 88% mp3
         D:\...\for-original\for-original
```

It looks in the repo root and in `training/`, picks the right speaker regex per
dataset, and writes `model_la.pkl` / `model_for.pkl`. Add `--limit 500` for a
quick check before committing to the full run.

`--limit` samples **across speakers**, not the first N rows — the ASVspoof
protocols are grouped by speaker, so `head`-style trimming yields a single
speaker and `train_detector.py` correctly refuses to split it.

The manual steps below still work and are what `--dataset` runs underneath.

## Step 1 — download ASVspoof 2019 LA

<https://datashare.ed.ac.uk/handle/10283/3336> — free, no signup, ~23 GB
zipped. You only need `LA.zip`.

Unzip it into `raw/` so you end up with:

```
training/raw/LA/ASVspoof2019_LA_train/flac/*.flac
training/raw/LA/ASVspoof2019_LA_cm_protocols/*.txt
```

## Step 2 — sort it into real/ and fake/

The dataset keeps every clip in one flat folder and puts the labels in a
separate protocol file. This reads the protocol and sorts them:

```
cd training
python prepare_asvspoof.py raw/LA --out corpus
```

Hard-links by default, so no second copy on disk. Add `--copy` if `raw/` is on
a different drive.

Start small to check it works: `--limit 500`.

## Step 3 — train and test

```
python "../Deepfake Voice Detection/detection/train_detector.py" corpus \
    --speaker-regex "(LA_\d+)" --save model.pkl
```

The regex matters — see "speaker leakage" below.

## Step 4 — read the score

**EER** (equal error rate) is the number. Lower is better.

| EER | means |
|-----|-------|
| ~50% | learned nothing, coin flip |
| 10-20% | working, weak |
| under 5% | decent for this size of model |
| under 1% | suspicious — check for leakage |

Ignore accuracy. ASVspoof is ~9x more spoof than bonafide, so a model that
guesses "fake" every time scores 90% accuracy and is useless.

## The audio decoder fallback

libsndfile 1.2.2 (what `soundfile` currently bundles) throws `unknown error in
flac decoder` on some FLAC files. The header reads fine, the audio does not, so
`sf.info()` succeeds and `sf.read()` fails. librosa then falls back to
`audioread`, which has no backend on a plain Windows install - and the clips are
dropped **silently**. In one 6-clip sample, 4 disappeared.

`train_detector.py` falls back to `pyav` when librosa raises, which decodes
these files and matches soundfile bit-for-bit on the ones both can read. It is
also what lets FoR's mp3 fakes load. If a corpus loads far fewer clips than it
has files, this is why - check that `av` is installed.

## Fake-or-Real (FoR)

Already sorted into `real/` and `fake/` with official `training/`,
`validation/` and `testing/` splits, so there is no protocol to parse and
nothing extra to download. Unzip it so you have:

```
for-original/for-original/training/{real,fake}
for-original/for-original/testing/{real,fake}
```

Then `--dataset for` (or pick it in the training tab). Measured here:

| run | EER |
|---|---|
| FoR train split, held-out speakers | 2.90% |
| **FoR official testing split** | **11.80%** |
| wav-only control (see below) | 0.84% |

**Two things to know before trusting the 2.90%.**

*Format imbalance.* `real/` is 100% wav, `fake/` is 88% mp3. A model can learn
"mp3 = fake" instead of learning synthesis. To check, train on the 3,263 wav
fakes only, where the format cue is gone — that scores 0.84% EER, so the signal
is real, not a codec artifact. Worth re-checking if you change the features.

*Filenames carry no speaker id* (`file123.wav`), so every clip becomes its own
speaker. The split cannot leak, but it cannot prove speaker independence
either.

## Forensics 0.3B — the pretrained option

The models trained here are small scikit-learn classifiers over MFCC features.
A pretrained transformer beats them by an order of magnitude, and the app can
use one as an alternative to anything trained above:

| | trained here (2019 LA) | Forensics 0.3B |
|---|---|---|
| ASVspoof 2019 LA | 11.11% EER | **0.26%** |
| In-the-Wild | — | **1.38%** |
| cross-dataset (LA → FoR) | 81% (inverted) | ~2% typical |

`eliya/forensics_0.3B_base_deepfake_classifier` — WavLM-large (~300M params)
plus AASIST graph attention, trained on 5M+ samples with heavy codec/noise/RIR
augmentation. That augmentation is why it does not fall into the bandwidth trap
below: compressed, band-limited audio is inside its training distribution.

**Licence: CC-BY-NC-4.0 — non-commercial.** Fine for coursework and research;
not for a commercial deployment.

### Install

```
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install transformers safetensors

# into "Deepfake Voice Detection/detection/media/forensics/"
curl -LO https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main/checkpoint_epoch_5.safetensors
curl -LO https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main/model.py
curl -LO https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main/config.json
```

The first run also pulls `microsoft/wavlm-large` (~1.2 GB) into the usual
Hugging Face cache. Skip the 3.8 GB `.pt` file — the `.safetensors` is the same
weights without the pickle.

Once those files exist it appears in the Models list as `forensics-0.3B` and can
be made active or picked per-clip like any other. It is entirely optional:
`cyber/forensics_model.py` imports torch only when the model is actually used,
so with the folder absent the app behaves exactly as before.

Expect **seconds per clip on CPU**, against milliseconds for the small models.
Good for uploads, poor for the live 4-second call segments.

## Testing a model — `--evaluate`

`--predict` scores one clip. `--evaluate` scores a whole labelled corpus, which
is what the FoR testing split is for:

```
python "../Deepfake Voice Detection/detection/train_detector.py" \
    --evaluate model_for.pkl path/to/testing_corpus
```

It prints EER, accuracy and per-class recall, and shows the model's own
reported EER next to the measured one. The gap is the point: the FoR model
reports 2.90% and measures 11.80% on the official test split.

**Cross-dataset is where models fall apart.** The 2019 LA model, which scores
11.11% on its own test set, scores **81.20%** on FoR — worse than a coin flip,
with fake recall of 7.6%. It is not weak on FoR, it is inverted, which is the
bandwidth trap below measured across datasets. Always evaluate on data the
model was not trained on before believing a number.

## Step 5 — try it on one clip

```
python "../Deepfake Voice Detection/detection/train_detector.py" \
    --predict model.pkl some_clip.wav
```

---

## Speaker leakage — the mistake that ruins this

If the same speaker appears in both training and testing, the model learns to
recognise *that person* rather than learning what synthetic speech sounds like.
The score comes out excellent and means nothing.

`train_detector.py` splits on speaker id taken from the start of the filename,
which is why `prepare_asvspoof.py` renames clips to `LA_0079-LA_T_1138215.flac`
and why you pass `--speaker-regex "(LA_\d+)"`.

If EER comes out near 0% on real data, suspect this first.

## The bandwidth trap — measured, not theoretical

The model trained on 2019 LA alone was tested against real call recordings from
`media/uploads/`. It called all of them fake:

| audio | median fake-probability |
|---|---|
| ASVspoof real | 0.001 |
| ASVspoof fake | 1.000 |
| real phone recordings | **0.999** |

The cause is bandwidth, not synthesis. Take a clip the model correctly scores
real and low-pass it like a phone codec:

```
real ASVspoof clip                 -> 0.0016  (real)
same clip, lowpassed to 3800 Hz    -> 0.9991  (fake)
```

Same voice, same speaker, only the bandwidth changed. ASVspoof 2019 is
full-band (rolloff ~5,500 Hz) in *both* classes, so "narrowband" never appears
during training and lands in unmapped feature space. It scores non-speech —
piano, a door chime — as 99.9% fake for the same reason.

Fixes, in order of effort: augment 2019 LA with codec/low-pass copies of both
classes; or train on FoR / In-the-Wild, which contain such audio natively.

## What this will and will not do

ASVspoof 2019 contains voice-cloning attacks from 2019. Cloning has improved a
lot since. A model can score well here and still miss something made with a
current tool.

Do not wire this into the app and call it solved. Once it trains, the next step
is testing it against fakes from another dataset (FoR, In-the-Wild) to see how
far it actually generalises — `--evaluate` does exactly that.

## Files

- `prepare_asvspoof.py` — sorts the dataset into `corpus/real` and `corpus/fake`
- `../Deepfake Voice Detection/detection/train_detector.py` — trains and scores
- `../Deepfake Voice Detection/detection/calibrate_thresholds.py` — unrelated;
  measures false-alarm rate of the app's current hand-tuned rules

Both scripts have a `--selftest` flag that runs on generated audio, no dataset
needed.
