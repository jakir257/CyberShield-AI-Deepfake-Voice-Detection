# CyberShield AI — Deepfake Voice Detection

**Presentation document.** What the system does, how it was built, what was
measured, and what it still cannot do.

> Every number in this document was produced by running the system. Where
> something is a published figure rather than one measured here, it says so.

---

## 1. The problem

A scam call in 2026 does not have to sound like a stranger. Voice cloning is
cheap enough that a caller can sound like your bank, your colleague, or a family
member. The victim's only defence is a judgement made in the first few seconds
of a call, using nothing but their ear.

This project asks a narrower, answerable question:

> Given a recorded call, can a machine say whether the voice was synthesised —
> and show its reasoning well enough that a human can disagree?

---

## 2. What was built

An Android phone captures calls and SMS and sends them to a PC on the same
wifi. The PC analyses them and shows the result on a dashboard.

```
   ANDROID PHONE                          WINDOWS PC
   (CallStream.apk)

   ┌───────────────┐   HTTP upload        ┌───────────────────────────────┐
   │ Record call   ├─────────────────────►│  Django :8000                 │
   │ Scan messages ├─────────────────────►│                               │
   └───────────────┘   JSON POST          │  • Audio pipeline             │
                                          │  • Deepfake detection         │
                                          │  • Scam-message scoring       │
                                          │  • URL inspection             │
                                          │  • Forensic report log        │
                                          └───────────────────────────────┘
```

Six working areas, reachable from one dashboard:

| Tab | What it does |
|---|---|
| **Voice Detection** | Upload or select a call → standardise → analyse → verdict |
| **Scam Message** | Score SMS in English, Hindi and Marathi with explanations |
| **URL Analysis** | Resolve a domain, fetch the page, judge what is actually there |
| **Forensic Reports** | Every analysis this system has run, with full detail |
| **Train Model** | Train a detector on a chosen dataset, then test it |
| **LLM** | Optional local second opinion via Ollama |

---

## 3. The deepfake detector — the core of the project

### 3.1 Three models, and why there is more than one

The system can score a clip with any of three detectors. They disagree, and the
disagreement is the interesting part.

| Model | What it is | EER |
|---|---|---|
| `voice_model.pkl` | trained here on ASVspoof 2019 LA | 10.95% |
| `model-for-…pkl` | trained here on Fake-or-Real | varies with run |
| `forensics-0.3B` | pretrained WavLM-large + AASIST, 300M params | 0.26%* |

\* published figure for ASVspoof 2019 LA, not measured here.

The first two are small scikit-learn classifiers over ~85 acoustic features
(MFCC means and standard deviations, spectral centroid, rolloff, flatness,
bandwidth, zero-crossing rate). They train on a laptop CPU in minutes.

The third is a pretrained transformer, ~1.2 GB of weights, optional.

### 3.2 The bandwidth trap — the most important finding

A model trained on ASVspoof 2019 LA scores **11.11% EER** on its own test set.
That looks respectable. Then it meets a real phone call:

| audio | verdict |
|---|---|
| ASVspoof real clip | real (0.001 fake) |
| ASVspoof fake clip | fake (1.000) |
| **a genuine phone recording** | **fake (0.999)** ✗ |

The cause is bandwidth, not synthesis. Take a clip the model correctly calls
real, and low-pass it like a phone codec:

```
real ASVspoof clip                 -> 0.0016  (real)
same clip, lowpassed to 3800 Hz    -> 0.9991  (fake)
```

Same voice, same speaker — only the bandwidth changed. ASVspoof 2019 is
full-band in *both* classes, so "narrowband" never appears during training and
lands in unmapped feature space.

A real call from the test set measures **3,855 Hz rolloff**. It is exactly the
audio the model never saw.

**Measured cross-dataset, this is severe.** The 2019 LA model, evaluated on the
Fake-or-Real test set:

```
EER 81.20%   —  worse than a coin flip
fake recall 7.6%
```

It is not weak on unfamiliar data. It is *inverted*.

### 3.3 Why the pretrained model is in the project

`forensics-0.3B` trains with codec transcoding (mp3/aac/opus/µ-law/GSM), noise
and reverberation augmentation, so band-limited audio is inside its training
distribution rather than off the edge of it.

On the same real call, with the same pipeline:

| model | verdict on a genuine call |
|---|---|
| `voice_model.pkl` | **FAKE 99.9%** ✗ |
| `forensics-0.3B` | **REAL 11.2%** ✓ |

This is why the tab lets you choose. A single number would have hidden the
disagreement; showing both makes the limitation visible.

### 3.4 A second measurement trap, found while testing

The pretrained model scores a fixed 5-second window, and the reference
implementation takes the **centre** crop. A 35-second call is often ~45%
silence, and its midpoint landed in a silent gap — so the model was scoring dead
air.

| window | verdict |
|---|---|
| 0–5 s | REAL 9.5% |
| 10–15 s | REAL 19.4% |
| 20–25 s | REAL 7.7% |
| 28–33 s | REAL 9.3% |
| **whole file, centre crop** | **FAKE 81.8%** ← the bug |

Fixed by trimming silence first, then scoring five windows and taking the
**median** — not the mean, so one cough or beep cannot decide a call.

Regression-checked on 8 known clips afterwards: **8/8 correct**, reals 7–9%,
fakes 66–86%.

---

## 4. Training — and how not to fool yourself

### 4.1 Datasets

| Dataset | Size | Why |
|---|---|---|
| ASVspoof 2019 LA | ~23 GB | studio-clean, 2019 attacks |
| Fake-or-Real (FoR) | ~10 GB | balanced, real-world, ships its own splits |

Both are selectable in the Train Model tab, which reports what is actually on
disk rather than assuming.

### 4.2 Speaker leakage — the mistake that invalidates everything

If the same speaker appears in training and testing, the model learns to
recognise *that person*, not synthesis. The score comes out excellent and means
nothing.

Every split is by speaker id, never by clip.

**This caught a real bug.** The clip limit originally took the first N rows —
but the ASVspoof protocols are grouped by speaker, so `--limit 40` put all 40
clips on **one speaker** and training correctly refused to split. The limit now
samples across speakers; the same run gives all 20.

### 4.3 The format trap in FoR

FoR is balanced 26,941 per class — but `real/` is 100% wav and `fake/` is 88%
mp3. A model could learn "mp3 = fake" and score beautifully while learning
nothing about synthesis.

Tested by training on the 3,263 **wav-only** fakes, where the format cue is
gone:

```
mixed formats     2.90% EER
wav-only control  0.84% EER   ← signal is real, not a codec artifact
```

### 4.4 EER, not accuracy

ASVspoof is ~9:1 spoof to bonafide. A model that answers "fake" every time
scores **90% accuracy** and is worthless. Equal error rate is the number that
cannot be gamed that way.

And a model's own test score is not evidence of much:

| | reported by the model | measured on a held-out set |
|---|---|---|
| FoR model | 2.90% | **11.80%** |
| 2019 LA model | 11.11% | **81.20%** (on FoR) |

`--evaluate` exists to make that second column easy to produce.

---

## 5. The forensic log

The Forensic Reports tab used to be three hardcoded rows dated 2023, including
a permanent "DeepFake Detected (94.8%)". It is now a real history.

Every analysed clip writes one immutable `ForensicReport` row:

| column | source |
|---|---|
| Timestamp | when the analysis ran |
| Evidence | the real filename, tagged `phone` or `browser` |
| Risk level | banded: CRITICAL ≥90, HIGH ≥70, MEDIUM ≥50, LOW ≥25, SAFE below |
| Detection result | the verdict **and which model produced it** |
| View Report | full detail |

**View Report** shows the timestamp, source, duration, fake probability, the
model with its EER and cutoff, the measured acoustics, and a player for the
audio — so a verdict can be checked rather than believed.

Rows are never edited. Re-analysing adds a row, which is what makes the log
usable as evidence — and it is how the same call can honestly show:

```
SAFE      Genuine voice (11.2% fake)     forensics-0.3B
CRITICAL  DeepFake detected (99.9%)      voice_model.pkl
```

---

## 6. The rest of the system

### 6.1 Audio pipeline

`standardise → denoise → trim silence → segment into 4 s windows`

Standardising to 16 kHz mono WAV is what makes every later measurement
comparable. Acoustic features are measured on the standardised audio **before**
denoising, because denoising reshapes the spectrum and would corrupt the
bandwidth reading the detector depends on.

Decoding uses `soundfile` with a `pyav` fallback — no ffmpeg. That was a
deliberate change: pydub shells out to ffmpeg, and on a machine without it
every mp3/m4a upload failed with `[WinError 2]`. WAV worked, which made the bug
look random. The fallback also reads FLAC files that libsndfile rejects
outright.

### 6.2 Scam message detection

Rule-based scoring with visible reasons, in English, Hindi and Marathi.
Measured **6/6** on real bank/university SMS plus synthetic scams, and **7/7**
on a mixed-language suite.

### 6.3 URL inspection

Heuristics plus an actual visit: DNS, TLS, redirect chain, page title. It
judges what is really at the end of a link, not what the link looks like.

### 6.4 Local LLM

An optional second opinion via Ollama. It is deliberately **never the
authority**: it can raise an alarm the rules missed, and it cannot talk one
down. When it called a phishing SMS harmless, the link scan overruled it —
because that finding came from resolving a domain, and the model's came from
reading a sentence.

---

## 7. Honest status

**Working, verified by running it**

- Deepfake detection with three selectable models, per-clip or as the default
- Training from the dashboard on two datasets, with speaker-safe splits
- Cross-dataset evaluation (`--evaluate`)
- Forensic history with full per-report detail
- Audio pipeline on real Samsung `.m4a` call recordings
- Scam SMS scoring with explanations in three languages
- URL inspection with a live visit
- Local LLM second opinion

**Deliberately optional**

- `forensics-0.3B` — 3.3 GB and CC-BY-NC-4.0 (non-commercial). The app imports
  torch only when the model is used, so the folder can simply be deleted.

**Not implemented**

- **Live call capture.** Blocked by Android at the OS level; VoIP is the route.
- **Domain age / reputation lookup.** Needs WHOIS or a threat feed.
- **Twilio in production.** Needs a public `wss://` URL and signature checks.

**Still mock**

- The dashboard overview statistics (Total Scans 1,284 etc.) are placeholders.
  The Forensic tab is now real; this row of tiles is not.

**Limits worth stating plainly**

- The small models are trained on 2019-era attacks. Cloning has improved. A
  model can score well here and miss something made with a current tool.
- FoR filenames carry no speaker id, so every clip becomes its own speaker. The
  split cannot leak, but it cannot prove speaker independence either.
- `forensics-0.3B`'s 0.26% is a published figure, not one measured here.
- CPU inference is ~12 s for the first clip of a session, ~3 s after. Fine for
  uploads, too slow for live 4-second segments.

---

## 8. What this project is really about

The most valuable change was not a feature. It was replacing invented numbers
with measured ones, and showing the reasoning next to the result.

A dashboard reading `94.8%` for every file looks more finished than one reading
`0/100 — no indicators matched`. It is also useless, and dangerous if anyone
acts on it.

The same discipline produced every finding in this document:

- The 11% model looked fine until it was asked about a real phone call.
- The FoR score looked excellent until the format imbalance was tested.
- The pretrained model looked wrong on a real call until the silence was
  measured.
- Three of those were found by **testing the thing on data it had not been
  tuned for** — which is the only test that ever tells you something.

A detector that says "fake" with 99.9% confidence about a genuine call is worse
than no detector, because someone will believe it. The system now shows which
model said what, on what evidence, and lets a human disagree.

Where something isn't built, the interface says so.

---

## 9. Running it

```
cd "Deepfake Voice Detection"
setup.bat      # once: folders, virtualenv, packages, optional model
run.bat        # every launch
```

Opens <http://127.0.0.1:8000/>, and binds `0.0.0.0` so the phone can reach it.
Full detail, including the optional model and troubleshooting, is in
[SETUP.md](SETUP.md); dataset and training specifics are in
[training/README.md](training/README.md).
