"""Optional WavLM+AASIST deepfake detector (Forensics 0.3B).

A ~300M-parameter pretrained model that scores far better than the small
scikit-learn detector trained in the Train Model tab:

    ASVspoof 2019 LA   0.26% EER   (ours: 11.11%)
    In-the-Wild        1.38% EER
    average external   4.06% EER

It is optional on purpose. torch is a heavy dependency and CPU inference takes
seconds per clip, so nothing here is imported until someone actually picks this
model - the app runs exactly as before when the weights are absent.

Weights: eliya/forensics_0.3B_base_deepfake_classifier on Hugging Face,
licensed CC-BY-NC-4.0 (non-commercial). Dropped in media/forensics/.

Everything is lazy and cached: the first call pays the ~10s model load, later
calls reuse it.
"""

import json
import logging
import sys
import threading
from pathlib import Path

import numpy as np
from django.conf import settings
import torch

logger = logging.getLogger(__name__)

MODEL_DIR = Path(settings.MEDIA_ROOT) / "forensics"
BASE_WEIGHTS = MODEL_DIR / "checkpoint_epoch_5.safetensors"
FINE_TUNED_WEIGHTS = MODEL_DIR / "checkpoints" / "active.safetensors"

def weights_path():
    """Use the best fine-tuned checkpoint when present; otherwise use the original."""
    return FINE_TUNED_WEIGHTS if FINE_TUNED_WEIGHTS.is_file() else BASE_WEIGHTS

# Backward-compatible name used by older code.
WEIGHTS = BASE_WEIGHTS

# the name the dashboard shows; not a real .pkl, so it can never collide with
# a trained model file
NAME = "forensics-0.3B"
LABEL = "Forensics 0.3B (WavLM+AASIST)"

# The card says a threshold in this range suits most domains, rather than the
# 0.5 the bare sigmoid implies.
THRESHOLD = 0.5

METRICS = MODEL_DIR / "checkpoints" / "active_metrics.json"

def active_metrics():
    if not METRICS.is_file():
        return {}
    try:
        return json.loads(METRICS.read_text(encoding="utf-8"))
    except Exception:
        return {}

SECONDS = 5.0
SAMPLE_RATE = 16000

_lock = threading.Lock()
_model = None          # loaded once, reused; guarded by _lock


def available():
    """-> True when this model can actually run. Cheap: no torch import."""
    if not weights_path().is_file() or not (MODEL_DIR / "model.py").is_file():
        return False
    try:
        import torch            # noqa: F401
        import torchaudio       # noqa: F401
        import transformers     # noqa: F401
    except Exception:
        return False
    return True


def why_not():
    """-> a short reason it cannot run, or '' when it can."""
    if not (MODEL_DIR / "model.py").is_file():
        return "model.py missing from media/forensics/"
    if not WEIGHTS.is_file():
        return "weights not downloaded (checkpoint_epoch_5.safetensors)"
    try:
        import torch            # noqa: F401
        import torchaudio       # noqa: F401
        import transformers     # noqa: F401
    except Exception as exc:
        return "install torch/torchaudio/transformers (%s)" % exc
    return ""


def _load():
    """The model, loaded once. Caller holds nothing; this takes the lock."""
    global _model
    with _lock:
        if _model is not None:
            return _model

        import torch
        from safetensors.torch import load_file

        # model.py ships with the weights and imports WavLMModel itself
        if str(MODEL_DIR) not in sys.path:
            sys.path.insert(0, str(MODEL_DIR))
        from model import DeepfakeDetector

        net = DeepfakeDetector().eval()
        # strict=False keeps compatibility with the original training checkpoint.
        net.load_state_dict(load_file(str(weights_path())), strict=False)
        _model = net
        return _model


def _speech_only(audio, sr):
    """Drop the silent stretches. -> the speech, or the original if that fails.

    A real phone call is often ~half silence. The model scores a fixed 5s
    window, so on a long call that window can land almost entirely in dead air
    and the score then describes the silence rather than the voice. Measured on
    a 35s real call: the whole file scored 81.8% fake, while every window that
    actually contained speech scored 8-21%.
    """
    try:
        import librosa

        intervals = librosa.effects.split(audio, top_db=30)
        if len(intervals) == 0:
            return audio
        speech = np.concatenate([audio[a:b] for a, b in intervals])
        # keep the original unless enough speech survived to score
        return speech if len(speech) >= sr else audio
    except Exception:
        return audio


def _windows(audio, sr, seconds=SECONDS, want=5):
    """-> up to `want` evenly spaced `seconds`-long crops, loudest first.

    One crop is a lottery on where the speech happens to be; several and a
    median is stable. Short clips give a single (tiled) window."""
    n = int(seconds * sr)
    if len(audio) <= n:
        return [audio]

    starts = np.linspace(0, len(audio) - n, num=want, dtype=int)
    crops = [audio[s:s + n] for s in starts]
    # a window that is mostly silence tells us nothing, so prefer loud ones
    crops.sort(key=lambda c: float(np.sqrt((c.astype(np.float64) ** 2).mean())),
               reverse=True)
    return crops


def _audio(path):
    """-> [5s mono 16k tensors], each peak-normalised, loudest first.

    The card's own inference takes a single centre crop. That is fine for a
    5-second sample and wrong for a phone call: the centre of a real call is
    often silence, and the model then scores the dead air. So: drop the silence
    first, then score several windows and let predict() take the median.

    Short clips are tiled rather than zero-padded, matching the reference
    implementation.
    """
    import torch

    # Not torchaudio.load: torchaudio 2.11 delegates to torchcodec, which is a
    # further dependency and needs FFmpeg libraries. audio_preprocessing._decode
    # already returns mono float32 at 16 kHz via soundfile/pyav, handles the
    # mp3 and flac cases, and needs no extra install.
    from .audio_preprocessing import _decode

    audio, _sr = _decode(str(path), SAMPLE_RATE)
    if audio.size == 0:
        raise ValueError("empty audio")

    audio = _speech_only(audio, SAMPLE_RATE)

    n = int(SECONDS * SAMPLE_RATE)
    out = []
    for crop in _windows(audio, SAMPLE_RATE):
        wav = torch.from_numpy(np.ascontiguousarray(crop)).float()
        # normalise per window, so one loud burst elsewhere in the call cannot
        # flatten the window actually being scored
        wav = wav / (wav.abs().max() + 1e-8)
        if wav.shape[0] < n:
            wav = wav.repeat((n + wav.shape[0] - 1) // wav.shape[0])[:n]
        out.append(wav[:n])
    return out


def predict(path):
    """-> the same dict shape as model_training.predict(), or None.

    The model is trained with label 1 = real, so the fake probability is
    1 - sigmoid(logit)."""
    if not available():
        return None
    try:
        import torch

        net = _load()
        windows = _audio(path)
        with torch.no_grad():
            batch = torch.stack(windows)
            logits = net(batch).float().reshape(-1)
            bonafide = torch.sigmoid(logits)

        # median, not mean: one odd window (a cough, a beep, a burst of hold
        # music) should not decide the verdict for a whole call
        fake = float(1.0 - bonafide.median().item())

        # Training cutoff is stored as REAL probability.
        # Convert it to the equivalent FAKE probability cutoff.
        real_cutoff = float(active_metrics().get("threshold", 0.88712))
        fake_cutoff = 1.0 - real_cutoff

        return {
            "fake_probability": round(fake, 4),
            "verdict": "fake" if fake >= fake_cutoff else "real",
            "threshold": round(fake_cutoff, 4),
            "model_eer": (
                float(active_metrics()["eer"]) * 100
                if active_metrics().get("eer") is not None
                else None
            ),
            "model_name": NAME,
            "windows": len(windows),
        }
    except Exception:
        # a heavy optional model must never take an upload down, but a silent
        # None is impossible to debug - log it and let the caller fall back
        logger.exception("forensics predict failed for %s", path)
        return None


def reload():
    """Drop the cached model so a newly written active checkpoint is used."""
    global _model
    with _lock:
        _model = None


def describe():
    """One row for the dashboard's model list."""
    ok = available()
    info = {
        "name": NAME,
        "dataset": "",
        "dataset_name": ("fine-tuned: IndicVoices + ASVspoof 5 + MLAAD" if FINE_TUNED_WEIGHTS.is_file() else "pretrained - 5M+ samples (CC-BY-NC-4.0)"),
        "readable": ok,
        "pretrained": True,
        "eer": (float(active_metrics()["eer"])*100 if FINE_TUNED_WEIGHTS.is_file() and active_metrics().get("eer") is not None else (None if FINE_TUNED_WEIGHTS.is_file() else 0.26)),
        "threshold": float(active_metrics().get("threshold", THRESHOLD)),
        "size_kb": round(weights_path().stat().st_size / 1024, 1) if weights_path().is_file() else 0,
        "saved_at": weights_path().stat().st_mtime if weights_path().is_file() else 0,
        "fine_tuned": FINE_TUNED_WEIGHTS.is_file(),
    }
    if not ok:
        info["detail"] = why_not()
    return info
