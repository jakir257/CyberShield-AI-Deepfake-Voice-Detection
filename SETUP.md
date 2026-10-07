# CyberShield — setup and run

Windows. Python 3.9+ on PATH. Everything else the scripts handle.

## Quick start

```
cd "Deepfake Voice Detection"
setup.bat      # once, on a fresh machine
run.bat        # every time after that
```

`run.bat` opens <http://127.0.0.1:8000/> once the server is listening. It binds
`0.0.0.0:8000`, so a phone on the same wifi can reach it too — the sidebar shows
the address to type into the CallStream app.

Sign in with an existing account, or register from the sign-in page.

---

## What each script does

### `setup.bat` — run once

1. **Makes the folders the app writes into.** `media/uploads`, `media/processed`,
   `media/audio_segments`, `media/models`, `media/forensics`, plus `training/`
   and `raw/` for datasets. These are gitignored, so a fresh clone has none of
   them and a missing `media/` turns the first upload into a stack trace.
2. **Creates the virtualenv** at `Deepfake Voice Detection/.venv`.
3. **Installs the core packages** — django, librosa, soundfile, av, numpy,
   scipy, scikit-learn, joblib.
4. **PostgreSQL driver**, optional. Skipped silently if it will not build.
5. **Offers Forensics 0.3B**, the pretrained deepfake model — ~3.3 GB, so it
   asks first. Answer `n` to skip; re-run the script later to add it.

Safe to re-run. Every step is a no-op when it is already done.

### `run.bat` — every launch

Re-checks the core packages (pip is a no-op when satisfied, and this self-heals
a partial install), picks a database, migrates, opens the browser and serves.

**PostgreSQL if it can reach one, SQLite if not.** No configuration needed for
SQLite. If a Postgres server is listening on `localhost:5432`, it asks once for
the password and remembers it in `db_password.txt` (gitignored). Press Enter at
that prompt to stay on SQLite.

---

## No ffmpeg needed

Audio is decoded with **soundfile**, falling back to **pyav** — both installed
by pip, neither needs an external binary.

This was a deliberate change. The app used to decode with pydub, which shells
out to `ffmpeg`, and on a machine without it every mp3/m4a/flac upload failed
with:

```
[WinError 2] The system cannot find the file specified
```

WAV worked (pydub reads it natively), which made the bug look random. The pyav
fallback also handles FLAC files that libsndfile rejects outright.

---

## Optional: Forensics 0.3B

A pretrained 300M-parameter detector (WavLM-large + AASIST). Far more accurate
than the small models trained inside the app, and the only one that gets real
phone recordings right:

| | trained in-app (2019 LA) | Forensics 0.3B |
|---|---|---|
| ASVspoof 2019 LA | 11.11% EER | **0.26%** |
| a real 35s phone call | FAKE 99.9% ✗ | **REAL 11.2%** ✓ |

**Licence: CC-BY-NC-4.0 — non-commercial.** Fine for coursework and research.

`setup.bat` offers it. To add it later, run `setup.bat` again and answer `y`, or
do it by hand:

```
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install transformers safetensors
```

then put these in `detection\media\forensics\`:

```
https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main/checkpoint_epoch_5.safetensors
https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main/model.py
https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main/config.json
```

The weights are 1,269,932,956 bytes — a shorter file is a truncated download and
will fail to load.

**The very first score is slow.** It also downloads `microsoft/wavlm-large`
(~1.2 GB) into the Hugging Face cache and loads 1.3 GB of weights from disk, so
allow **several minutes** on a cold machine. After that the model stays resident
in the server process: ~7s for the first clip of a session, then under a second.
Restarting the server pays the load again (about 25s once the cache is warm).

It shows up in the Models list as `forensics-0.3B`. Nothing else changes: the
app imports torch only when this model is actually used, so deleting
`media\forensics\` returns it to exactly its previous behaviour.

**If it does not appear in the list**, torch is missing from the virtualenv the
app is running from. There can be two — `Deepfake Voice Detection\.venv` (the
one `run.bat` and `setup.bat` use) and a stray `.venv` at the repo root. Install
into the first one, or just re-run `setup.bat`, which always targets it. To
check:

```
"Deepfake Voice Detection\.venv\Scripts\python" -c "import torch; print(torch.__version__)"
```

---

## Training your own model

The Train Model tab lists whichever datasets are on disk. See
[training/README.md](training/README.md) for where to get them and what the
scores mean. Short version:

| dataset | put it at | notes |
|---|---|---|
| ASVspoof 2019 LA | `LA/` or `raw/LA/` | studio-clean; calls real phone audio fake |
| Fake-or-Real (FoR) | `for-original/for-original/` | balanced, already sorted, no extra download |

Pick one, optionally set a clip limit, press Start Training. The first run on a
dataset sorts it into `real/` and `fake/` first, so it takes longer to get going.

**Read the EER, not the accuracy**, and prefer a score measured on data the
model was not trained on — a model can score 11% on its own test set and 81% on
another dataset. `train_detector.py --evaluate` does that measurement.

---

## Troubleshooting

**"Python not found"** — install Python 3.9+ and tick *Add Python to PATH*.

**The page looks stale after an update** — the browser cached the old JavaScript.
Hard-refresh with `Ctrl+F5`. Assets are versioned (`?v=NN` in `index.html`), so
this should be rare.

**"Audio Processing Failed [WinError 2]"** — an old build that still used pydub.
Pull the current code; ffmpeg is no longer needed.

**A real recording is called fake** — expected from the small in-app models on
phone audio. They are trained on full-band studio recordings, so narrowband
calls (~3.8 kHz rolloff) land outside anything they learned. Use Forensics 0.3B
for call recordings.

**Training says "only 1 speakers"** — the clip limit was taken from one speaker.
Fixed in the current code, which samples across speakers; make sure you are not
on an old copy.

**`run.bat` seems to hang at startup** — it is waiting at the PostgreSQL
password prompt. That prompt only appears when a server is actually listening on
`localhost:5432` but no password has been saved yet. Press **Enter** to use
SQLite and it carries on. Run it in a visible terminal, not a hidden window, or
you will not see what it is asking.

**Postgres password prompt on every launch** — the password did not work, so
nothing was saved. Press Enter to use SQLite, or fix the password:

```
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "ALTER USER postgres PASSWORD 'newpass';"
```
