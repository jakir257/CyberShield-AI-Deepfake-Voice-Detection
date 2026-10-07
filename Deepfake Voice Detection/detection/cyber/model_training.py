"""Train the deepfake detector from the dashboard, and load the result.

Nothing here reimplements training. It runs ../train_detector.py as a
subprocess and keeps its output in memory so the browser can poll for it.
A subprocess is the lazy correct choice: training pegs a core for half an
hour, and Django's worker thread should not be the thing holding it.

One run at a time, process-wide. Two concurrent trainings would fight over
the same model.pkl and neither would win.
"""

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from django.conf import settings

# detection/ -> the repo root that holds training/ and train_detector.py
BASE = Path(settings.BASE_DIR)
TRAINER = BASE / "train_detector.py"
TRAINING_DIR = BASE.parent.parent / "training"
CORPUS = TRAINING_DIR / "corpus"
MODEL_DIR = Path(settings.MEDIA_ROOT) / "models"
ACTIVE_FILE = MODEL_DIR / "active.txt"

# Kept so the model trained before this was multi-model does not disappear.
LEGACY_PATH = Path(settings.MEDIA_ROOT) / "voice_model.pkl"

# ASVspoof filenames are LA_0079-LA_T_1138215.flac; group 1 is the speaker.
# Splitting on it is what stops the model scoring itself on voices it trained on.
SPEAKER_REGEX = r"(LA_\d+)"


def _prep():
    """training/prepare_asvspoof.py as a module. It owns where each dataset
    lives and how to prepare it; importing beats keeping a second copy of that
    table here and letting the two drift."""
    sys.path.insert(0, str(TRAINING_DIR))
    import prepare_asvspoof
    return prepare_asvspoof


def _dataset_keys():
    """Known dataset keys, longest first so 'for' cannot shadow a longer key
    that starts with it."""
    try:
        return sorted(_prep().DATASETS, key=len, reverse=True)
    except Exception:
        return []


def _corpus_of(key):
    """The corpus folder a dataset trains from, as a string."""
    try:
        return str(TRAINING_DIR / _prep().DATASETS[key]["out"])
    except Exception:
        return ""


def _dataset_name(key):
    """'for' -> 'Fake-or-Real (FoR)'. Empty for models trained before the
    filename carried a dataset."""
    if not key:
        return ""
    try:
        return _prep().DATASETS[key]["name"]
    except Exception:
        return key


def datasets():
    """-> [{key, name, note, corpus, ready, detail}] for the training tab.

    'ready' means trainable right now: either an already-prepared corpus/, or
    the raw dataset on disk waiting to be sorted."""
    try:
        prep = _prep()
    except Exception as exc:
        return [{"key": "", "name": "prepare_asvspoof.py not importable",
                 "note": str(exc), "ready": False, "detail": ""}]

    out = []
    for key, spec in prep.DATASETS.items():
        corpus = TRAINING_DIR / spec["out"]
        prepared = _corpus_clips(corpus)
        root, _keys, why = prep.locate(key)
        out.append({
            "key": key,
            "name": spec["name"],
            "note": spec["note"],
            "corpus": str(corpus),
            "prepared": prepared,          # clips already sorted, 0 if none
            "ready": bool(prepared or why is None),
            "detail": ("%s clips prepared" % f"{prepared:,}") if prepared
                      else (why or "ready to prepare from %s" % root),
            "url": spec.get("url", ""),
            "url_label": "Download %s" % spec.get("download", "dataset"),
            # show the link only while something is still missing
            "show_url": not (prepared or why is None),
        })
    return out


def _corpus_clips(corpus):
    """Clips already sorted into a corpus dir. 0 when it does not exist."""
    total = 0
    for side in ("real", "fake"):
        try:
            total += sum(1 for f in (corpus / side).iterdir()
                         if f.suffix.lower() in AUDIO_EXT)
        except OSError:
            return 0
    return total

AUDIO_EXT = {".wav", ".flac", ".m4a", ".mp3", ".ogg", ".opus"}

MAX_LINES = 500  # a full run prints ~150 lines; the cap is just anti-runaway

# train_detector prints "    1400/2580" while extracting features, and
# "  real: 2580 clips" when it starts a class. That is enough to derive real
# progress; nothing needs to be added to the trainer itself.
COUNT_RE = re.compile(r"^\s+(\d+)/(\d+)\s*$")
CLASS_RE = re.compile(r"^\s+(real|fake): (\d+) clips")

_lock = threading.Lock()
_proc = None          # the live subprocess, so stop() can kill it

_BLANK = {
    "state": "idle",      # idle | running | done | error | stopped
    "lines": [],
    "started": None,
    "finished": None,
    "error": None,
    "phase": "",          # what it is doing right now, in words
    "done_clips": 0,      # features extracted so far, across both classes
    "total_clips": 0,     # 0 until the first class announces its size
}

_run = dict(_BLANK, lines=[])


def _append(text):
    with _lock:
        _run["lines"].append(text)
        if len(_run["lines"]) > MAX_LINES:
            del _run["lines"][:-MAX_LINES]
        _track(text)


def _track(line):
    """Update phase/progress from a trainer output line. Caller holds the lock.

    The two classes are counted one after the other, so the running total is
    (clips finished in earlier classes) + (position within this one)."""

    m = CLASS_RE.match(line)
    if m:
        which, n = m.group(1), int(m.group(2))
        # the count that just finished becomes the base for the next class
        _run["_base"] = _run.get("done_clips", 0)
        _run["phase"] = "reading %s clips (%s)" % (which, f"{n:,}")

        # "real" is announced first and "fake" only much later. Adding them as
        # they arrive would run the bar to 100% and then jump it backwards, so
        # count both folders once, on the first announcement, and keep that.
        if not _run.get("_counted"):
            _run["_counted"] = True
            _run["total_clips"] = _run.get("total_clips") or n
        return

    m = COUNT_RE.match(line)
    if m:
        _run["done_clips"] = _run.get("_base", 0) + int(m.group(1))
        return

    stripped = line.strip()
    if stripped.startswith("Loading audio"):
        _run["phase"] = "extracting audio features"
    elif stripped.startswith("train ") and "clips" in stripped:
        # feature extraction is over; the fit is the next (short) phase
        _run["done_clips"] = _run.get("total_clips", 0)
        _run["phase"] = "fitting the model"
    elif stripped.startswith("=== held-out"):
        _run["phase"] = "scoring on held-out speakers"
    elif stripped.startswith("Saved "):
        _run["phase"] = "saving the model"


def _prepare_corpus(key, limit):
    """Sort the raw dataset into corpus/ before training. No-op when it is
    already sorted. -> (ok, message)."""
    prep = _prep()
    spec = prep.DATASETS[key]
    corpus = TRAINING_DIR / spec["out"]
    root, keys, why = prep.locate(key)
    if why:
        return False, why

    _run["phase"] = "sorting %s into real/ and fake/" % spec["name"]
    _append("Preparing %s from %s" % (spec["name"], root))
    try:
        if "folders" in spec:      # already sorted (FoR), just link it
            prep.prepare_folders(root, corpus, spec["folders"], False, limit)
        else:
            prep.prepare(root, corpus, ["train"], False, limit, keys)
    except SystemExit as exc:      # prepare() calls sys.exit on bad input
        return False, str(exc)
    _append("")
    return True, "ok"


def _train(limit):
    key = _run.get("_dataset") or "la"
    spec = _prep().DATASETS[key]

    # sorting is cheap next to training and makes the corpus match the chosen
    # dataset; without it "train for" would silently train the LA corpus
    ok, message = _prepare_corpus(key, limit)
    if not ok:
        with _lock:
            _run["state"] = "error"
            _run["error"] = message
            _run["phase"] = "failed"
            _run["finished"] = time.time()
        _append("FAILED: %s" % message)
        return

    corpus = TRAINING_DIR / spec["out"]
    with _lock:
        _run["_counted"] = False
        _run["total_clips"] = _corpus_clips(corpus)

    cmd = [
        sys.executable, str(TRAINER), str(corpus),
        "--speaker-regex", spec["regex"],
        "--save", str(_run["_target"]),
    ]
    if limit:
        cmd += ["--limit", str(limit)]

    _append("$ " + " ".join(cmd))
    _append("")

    global _proc

    try:
        # line-buffered, stderr folded in: train_detector prints progress to
        # stderr and results to stdout, and the dashboard wants both in order
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        with _lock:
            _proc = proc

        for line in proc.stdout:
            _append(line.rstrip())

        code = proc.wait()
        with _lock:
            _proc = None

    except Exception as exc:
        with _lock:
            _run["state"] = "error"
            _run["error"] = str(exc)
            _run["finished"] = time.time()
        _append("FAILED: %s" % exc)
        return

    with _lock:
        _run["finished"] = time.time()
        stopped = _run.get("_stopping")
        if stopped:
            _run["state"] = "stopped"
            _run["phase"] = "stopped before it finished"
        elif code == 0 and _run["_target"].is_file():
            _run["state"] = "done"
            _run["phase"] = "finished"
        else:
            _run["state"] = "error"
            _run["phase"] = "failed"
            _run["error"] = "trainer exited %d" % code

    if stopped:
        _append("")
        _append("Stopped. The previous model, if any, is untouched.")
        return

    if code == 0:
        # a fresh model becomes the active one; that is what "train" means here
        set_active(_run["_target"].name)
        _append("")
        _append("Saved as %s and made active." % _run["_target"].name)
        _append("It is now used for every upload. No restart needed.")


def start(limit=None, dataset="la"):
    """-> (started, message). Refuses a second concurrent run."""
    dataset = dataset or "la"
    try:
        specs = _prep().DATASETS
    except Exception as exc:
        return False, "Cannot read the dataset list: %s" % exc
    if dataset not in specs:
        return False, "Unknown dataset: %s" % dataset

    # fail here, with a message the tab can show, rather than inside the thread
    ready = next((d for d in datasets() if d["key"] == dataset), None)
    if ready and not ready["ready"]:
        return False, "%s is not ready: %s" % (ready["name"], ready["detail"])

    with _lock:
        if _run["state"] == "running":
            return False, "Training is already running."
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = MODEL_DIR / ("model-%s-%s%s.pkl" % (
            dataset, stamp, "-limit%d" % limit if limit else ""))

        _run.clear()
        _run.update(_BLANK, lines=[], state="running", started=time.time(),
                    phase="starting", _stopping=False, _limit=limit,
                    _target=target, _dataset=dataset)

    threading.Thread(target=_train, args=(limit,), daemon=True).start()
    return True, "Training started on %s." % specs[dataset]["name"]


def stop():
    """-> (stopped, message). Kills the trainer and its children."""
    with _lock:
        if _run["state"] != "running" or _proc is None:
            return False, "Nothing is training."
        _run["_stopping"] = True
        proc = _proc

    _append("")
    _append("Stopping...")

    if os.name == "nt":
        # librosa/sklearn spawn worker processes; proc.kill() would orphan them
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.kill()

    return True, "Training stopped."


def status():
    with _lock:
        started = _run["started"]
        finished = _run["finished"]
        done = _run.get("done_clips", 0)
        total = _run.get("total_clips", 0)
        running = _run["state"] == "running"

        elapsed = round((finished or time.time()) - started, 1) if started else 0.0

        # ETA from the rate actually measured so far, not from a guess about
        # how fast this machine is. Meaningless before a few clips are done.
        eta = None
        if running and done > 20 and total and elapsed > 0:
            per_clip = elapsed / done
            eta = round(max(0.0, (total - done) * per_clip), 1)

        return {
            "state": _run["state"],
            "lines": list(_run["lines"]),
            "error": _run["error"],
            "phase": _run.get("phase", ""),
            "done_clips": done,
            "total_clips": total,
            "percent": round(done / total * 100, 1) if total else 0.0,
            "elapsed": elapsed,
            "eta": eta,
            "dataset": _run.get("_dataset", ""),
            "model": model_info(),
        }


# ---------------------------------------------------------------------------
# using the trained model
# ---------------------------------------------------------------------------

from functools import lru_cache


def _models():
    """-> [Path] newest first. The legacy single-file model counts as one."""
    found = []
    if MODEL_DIR.is_dir():
        found += [f for f in MODEL_DIR.glob("*.pkl") if f.is_file()]
    if LEGACY_PATH.is_file():
        found.append(LEGACY_PATH)
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)


def _resolve(name):
    """Model name -> Path, or None. Name only, never a path: this value
    arrives from the browser and must not be able to point outside MODEL_DIR."""
    if not name:
        return None
    name = os.path.basename(str(name))
    for f in _models():
        if f.name == name:
            return f
    return None


def active_path():
    """The model predictions currently use. Falls back to the newest."""
    if ACTIVE_FILE.is_file():
        chosen = _resolve(ACTIVE_FILE.read_text(encoding="utf-8").strip())
        if chosen:
            return chosen
    models = _models()
    return models[0] if models else None


def set_active(name):
    """-> (ok, message). Writing the pointer is what switches the model."""
    from . import forensics_model
    if name == forensics_model.NAME:
        if not forensics_model.available():
            return False, "Forensics model is not ready: %s" % forensics_model.why_not()
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE_FILE.write_text(name, encoding="utf-8")
        load_model.cache_clear()
        return True, "Now using %s" % forensics_model.LABEL

    target = _resolve(name)
    if not target:
        return False, "No such model: %s" % name
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(target.name, encoding="utf-8")
    load_model.cache_clear()
    return True, "Now using %s" % target.name


@lru_cache(maxsize=4)
def _load(path_str, mtime):
    """mtime is part of the key so a retrained file is never served stale."""
    try:
        import joblib
        return joblib.load(path_str)
    except Exception:
        return None


def load_model():
    """-> bundle dict, or None when nothing has been trained yet."""
    path = active_path()
    if not path:
        return None
    return _load(str(path), path.stat().st_mtime)


# kept as an attribute so callers can still clear the cache by name
load_model.cache_clear = _load.cache_clear


def describe(path):
    """One model, as the dashboard shows it."""
    bundle = _load(str(path), path.stat().st_mtime)

    # model-<dataset>-<stamp>.pkl. Models trained before this, or renamed, fall
    # back to the corpus path recorded inside the bundle.
    key = next((k for k in _dataset_keys()
                if path.name.startswith("model-%s-" % k)), "")
    if not key and bundle:
        corpus = str(bundle.get("corpus", ""))
        key = next((k for k in _dataset_keys()
                    if corpus and _corpus_of(k) == corpus), "")

    info = {
        "name": path.name,
        "saved_at": path.stat().st_mtime,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "readable": bundle is not None,
        "dataset": key,
        "dataset_name": _dataset_name(key),
    }
    if bundle:
        info["clips"] = bundle.get("clips")
        info["speakers"] = bundle.get("speakers")
        info["eer"] = round(bundle.get("eer", 0) * 100, 2)
        info["threshold"] = round(bundle.get("threshold", 0.5), 3)
    return info


def list_models():
    """-> [model info] newest first, with the active one flagged.

    The pretrained Forensics model is listed alongside the trained ones when
    its weights are present; it is a different kind of thing, so it is tagged
    rather than merged into the .pkl list."""
    from . import forensics_model

    # When the pretrained model is selected, active_path() cannot resolve its
    # name and falls back to the newest .pkl - which would then also be flagged
    # active. Exactly one row may carry the flag.
    fx_active = _active_name() == forensics_model.NAME
    current = None if fx_active else active_path()

    out = []
    for f in _models():
        d = describe(f)
        d["active"] = bool(current and f.name == current.name)
        out.append(d)

    if forensics_model.available():
        d = forensics_model.describe()
        d["active"] = _active_name() == forensics_model.NAME
        out.append(d)
    return out


def _active_name():
    """The selected model's name, which may be the pretrained one."""
    if ACTIVE_FILE.is_file():
        return ACTIVE_FILE.read_text(encoding="utf-8").strip()
    return ""


def delete_model(name):
    """-> (ok, message). Refuses to delete the model in use."""
    from . import forensics_model
    if name == forensics_model.NAME:
        return False, ("The pretrained model is not deletable from here - "
                       "remove media/forensics/ to drop it.")

    target = _resolve(name)
    if not target:
        return False, "No such model: %s" % name
    current = active_path()
    if current and current.name == target.name:
        return False, "That model is active. Select another one first."
    try:
        target.unlink()
    except OSError as exc:
        return False, str(exc)
    load_model.cache_clear()
    return True, "Deleted %s" % target.name


def model_info():
    """The active model, in the shape the training tab already renders."""
    from . import forensics_model
    if _active_name() == forensics_model.NAME and forensics_model.available():
        d = forensics_model.describe()
        return {
            "trained": True,
            "name": d["name"],
            "eer": d["eer"],
            "threshold": d["threshold"],
            "saved_at": d["saved_at"],
            "size_kb": d["size_kb"],
            "count": len(_models()) + 1,
        }

    path = active_path()
    bundle = load_model()
    if not path or not bundle:
        return {"trained": False, "count": len(_models())}
    return {
        "trained": True,
        "name": path.name,
        "eer": round(bundle.get("eer", 0) * 100, 2),
        "threshold": round(bundle.get("threshold", 0.5), 3),
        "saved_at": path.stat().st_mtime,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "count": len(_models()),
    }


def predict(path, model_name=None):
    """-> {probability, verdict, ...} or None if there is no model.

    model_name picks one model for this call only, without changing what the
    rest of the app uses - that is what the Voice Detection picker needs.
    Raises nothing: a broken model must not take an upload down with it."""
    from . import forensics_model

    # the pretrained model is not a .pkl; route it before touching _resolve
    if model_name == forensics_model.NAME:
        return forensics_model.predict(path)
    if not model_name and _active_name() == forensics_model.NAME:
        return forensics_model.predict(path)

    if model_name:
        chosen = _resolve(model_name)
        if not chosen:
            return None
        bundle = _load(str(chosen), chosen.stat().st_mtime)
        used = chosen.name
    else:
        bundle = load_model()
        current = active_path()
        used = current.name if current else None

    if not bundle:
        return None
    try:
        # the feature extractor that produced the training data, reused so
        # the model is never fed features shaped differently than it learned
        sys.path.insert(0, str(BASE))
        from train_detector import features

        thr = bundle.get("threshold", 0.5)
        p = float(bundle["model"].predict_proba(features(path)[None, :])[0, 1])
        return {
            "fake_probability": round(p, 4),
            "verdict": "fake" if p > thr else "real",
            "threshold": round(thr, 3),
            "model_eer": round(bundle.get("eer", 0) * 100, 2),
            "model_name": used,
        }
    except Exception:
        return None
