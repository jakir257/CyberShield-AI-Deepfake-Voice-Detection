"""
Explainable AI for the voice detector.

Supports:
    1. Forensics 0.3B waveform model:
       SHAP GradientExplainer over the raw waveform.

    2. Scikit-learn detectors:
       SHAP TreeExplainer over the 85-dimensional feature vector.

Also provides:
    - contributing audio regions
    - log-mel spectrogram
    - readable explanation narrative

The Forensics 0.3B model consumes raw waveform rather than the
85-dimensional handcrafted feature vector, so its explanation is
based on waveform/time regions rather than MFCC feature names.
"""

import io
import logging
import os
import base64

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scikit-learn detector feature names
# ---------------------------------------------------------------------------

# train_detector.features() order:
#   0-19   MFCC mean
#   20-39  MFCC std
#   40-59  MFCC delta mean
#   60-79  MFCC delta std
#   80-84  spectral stats

_SPECTRAL = [
    "spectral centroid",
    "spectral rolloff",
    "spectral flatness",
    "spectral bandwidth",
    "zero-crossing rate",
]


def feature_names():
    """-> 85 human-readable names in features() order."""
    names = []

    for label in (
        "MFCC %d mean",
        "MFCC %d std",
        "MFCC-delta %d mean",
        "MFCC-delta %d std",
    ):
        names += [label % i for i in range(1, 21)]

    return names + list(_SPECTRAL)


def _band_of(name):
    """-> readable coarse frequency band for an MFCC coefficient."""

    if "MFCC" not in name:
        return "full spectrum"

    try:
        n = int(
            "".join(
                ch
                for ch in name.split()[1]
                if ch.isdigit()
            )
        )
    except (IndexError, ValueError):
        return "full spectrum"

    if n <= 4:
        return "low-frequency regions"

    if n <= 12:
        return "mid-frequency regions"

    return "higher-frequency regions"


# ---------------------------------------------------------------------------
# Scikit-learn contributing windows
# ---------------------------------------------------------------------------

def contributing_windows(
    path,
    model,
    threshold,
    seconds=0.6,
    top=3,
):
    """
    -> [(start, end, fake_probability)] for the most decisive windows.

    This is used by the scikit-learn detector.

    Each window is actually scored by the detector. The result is therefore
    based on the model's output for that audio slice rather than an
    interpolation of the whole-file probability.
    """

    import sys
    from django.conf import settings

    sys.path.insert(0, str(settings.BASE_DIR))

    from train_detector import _load_audio, features

    import soundfile as sf
    import tempfile

    audio, sr = _load_audio(path)

    span = int(seconds * sr)

    if audio.size < span * 2:
        return []

    hop = max(1, span // 2)

    scored = []

    tmpdir = tempfile.mkdtemp()

    try:
        for i, start in enumerate(
            range(
                0,
                audio.size - span + 1,
                hop,
            )
        ):
            chunk = audio[start:start + span]

            wav = os.path.join(
                tmpdir,
                "w%d.wav" % i,
            )

            sf.write(
                wav,
                chunk,
                sr,
            )

            try:
                x = features(wav)[None, :]

                p = float(
                    model.predict_proba(x)[0, 1]
                )

            except Exception:
                continue

            finally:
                try:
                    os.remove(wav)
                except OSError:
                    pass

            scored.append(
                (
                    start / sr,
                    (start + span) / sr,
                    round(p, 4),
                )
            )

    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    if not scored:
        return []

    mean = sum(
        s[2] for s in scored
    ) / len(scored)

    scored.sort(
        key=lambda s: abs(s[2] - mean),
        reverse=True,
    )

    picked = []

    for a, b, p in scored:

        if all(
            b <= pa or a >= pb
            for pa, pb, _ in picked
        ):
            picked.append(
                (
                    a,
                    b,
                    p,
                )
            )

        if len(picked) == top:
            break

    picked.sort(
        key=lambda s: s[0]
    )

    return [
        (
            round(a, 2),
            round(b, 2),
            p,
        )
        for a, b, p in picked
    ]


# ---------------------------------------------------------------------------
# SHAP for scikit-learn detector
# ---------------------------------------------------------------------------

def shap_top_features(
    x,
    model,
    background=None,
    top=6,
):
    """
    -> [(name, signed_contribution)] for the features that moved
       the prediction most.

    Positive values push toward fake.
    Negative values push toward real.

    TreeExplainer is used because the scikit-learn detector is a tree model.
    """

    try:
        import shap

    except Exception:
        return None

    try:
        explainer = shap.TreeExplainer(model)

        values = explainer.shap_values(
            x,
            check_additivity=False,
        )

        arr = np.asarray(values)

        # Some SHAP versions return:
        #     [class_0, class_1]
        # while others return:
        #     samples x features x classes
        if arr.ndim == 3:

            if arr.shape[-1] == 2:
                arr = arr[..., 1]

            else:
                arr = arr[0]

        row = np.asarray(
            arr
        ).reshape(-1)[:85]

        names = feature_names()

        pairs = [
            (
                names[i],
                float(row[i]),
            )
            for i in range(
                min(
                    len(names),
                    row.size,
                )
            )
        ]

        pairs.sort(
            key=lambda p: abs(p[1]),
            reverse=True,
        )

        return pairs[:top]

    except Exception:
        logger.exception(
            "shap explanation failed"
        )

        return None


# ---------------------------------------------------------------------------
# SHAP for actual Forensics 0.3B model
# ---------------------------------------------------------------------------

def shap_forensics_clip(path, top=3):
    """
    SHAP explanation for the actual Forensics 0.3B waveform model.

    Instead of calculating SHAP for every one of the ~80,000 waveform
    samples in a 5-second window, the waveform is divided into a small
    number of temporal regions.

    SHAP then explains how those temporal regions influenced the model.
    """

    try:
        import shap
        import torch

        from . import forensics_model

        if not forensics_model.available():
            logger.warning("Forensics model is not available for SHAP")
            return None

        net = forensics_model._load()
        windows = forensics_model._audio(path)

        if not windows:
            logger.warning("No Forensics audio windows available for SHAP")
            return None

        net.eval()

        # ------------------------------------------------------------
        # Find the model device
        # ------------------------------------------------------------

        try:
            device = next(net.parameters()).device
        except StopIteration:
            device = torch.device("cpu")

        # ------------------------------------------------------------
        # The real detector uses the median prediction of its windows.
        #
        # Instead of running expensive SHAP on all windows, identify
        # the window whose prediction is closest to that median.
        #
        # This keeps the explanation tied to the actual final decision
        # while dramatically reducing memory usage.
        # ------------------------------------------------------------

        with torch.no_grad():
            batch = torch.stack(windows).float().to(device)

            logits = net(batch).float().reshape(-1)

            median_logit = torch.median(logits)

            distances = torch.abs(logits - median_logit)

            representative_index = int(
                torch.argmin(distances).item()
            )

        waveform = (
            windows[representative_index]
            .detach()
            .clone()
            .float()
            .to(device)
        )

        # [samples] -> [1, samples]
        waveform = waveform.unsqueeze(0)

        sample_count = waveform.shape[1]

        # ------------------------------------------------------------
        # Divide the 5-second waveform into 12 temporal regions.
        #
        # 12 regions keeps SHAP computationally manageable while
        # providing useful time-localized explanations.
        # ------------------------------------------------------------

        region_count = 12

        region_edges = torch.linspace(
            0,
            sample_count,
            region_count + 1,
            device=device,
            dtype=torch.long,
        )

        # ------------------------------------------------------------
        # SHAP model
        #
        # Input:
        #     12 mask values
        #
        # Each mask value controls one temporal region of the
        # original waveform.
        #
        # Therefore SHAP explains temporal regions rather than
        # 80,000 individual waveform samples.
        # ------------------------------------------------------------

        class TemporalMaskModel(torch.nn.Module):

            def __init__(self, model, original_waveform):
                super().__init__()

                self.model = model

                self.register_buffer(
                    "original_waveform",
                    original_waveform,
                )

            def forward(self, masks):

                # masks: [batch, 12]

                batch_size = masks.shape[0]

                pieces = []

                for i in range(region_count):

                    start = int(
                        region_edges[i].item()
                    )

                    end = int(
                        region_edges[i + 1].item()
                    )

                    # Take the original waveform section.
                    piece = self.original_waveform[:, start:end]

                    # Expand it for the current SHAP batch.
                    piece = piece.expand(
                        batch_size,
                        -1
                    )

                    # Apply the temporal mask WITHOUT inplace operations.
                    piece = (
                        piece
                        * masks[:, i:i + 1]
                    )

                    pieces.append(piece)

                # Join all temporal regions into the complete waveform.
                audio = torch.cat(
                    pieces,
                    dim=1
                )

                output = self.model(audio)

                return output.reshape(-1, 1)

        shap_model = TemporalMaskModel(
            net,
            waveform,
        ).to(device)

        shap_model.eval()

        # ------------------------------------------------------------
        # Background:
        #
        # all regions disabled.
        #
        # This represents the absence of the original waveform.
        # ------------------------------------------------------------

        background = torch.zeros(
            (1, region_count),
            device=device,
            dtype=torch.float32,
        )

        # The actual waveform with all regions enabled.
        sample = torch.ones(
            (1, region_count),
            device=device,
            dtype=torch.float32,
        )

        # ------------------------------------------------------------
        # Gradient SHAP
        #
        # Keep nsamples small because the underlying WavLM model is
        # large and the waveform is still 5 seconds long.
        # ------------------------------------------------------------

        explainer = shap.GradientExplainer(
            shap_model,
            background,
        )

        shap_values = explainer.shap_values(
            sample,
            nsamples=12,
        )

        # ------------------------------------------------------------
        # Normalize SHAP output format across SHAP versions.
        # ------------------------------------------------------------

        if isinstance(shap_values, list):
            shap_values = shap_values[0]

        shap_values = np.asarray(shap_values)

        shap_values = np.squeeze(shap_values)

        if shap_values.ndim != 1:
            shap_values = shap_values.reshape(-1)

        if shap_values.size != region_count:
            logger.warning(
                "Temporal SHAP size mismatch: %d != %d",
                shap_values.size,
                region_count,
            )
            return None

        # ------------------------------------------------------------
        # Model semantics:
        #
        # sigmoid(logit) = REAL probability
        #
        # Therefore:
        #
        # positive SHAP -> real
        # negative SHAP -> fake
        #
        # Invert the sign so positive contribution means
        # AI-generated.
        # ------------------------------------------------------------

        fake_shap = -shap_values

        sr = forensics_model.SAMPLE_RATE

        regions = []

        for i in range(region_count):

            start_sample = int(
                region_edges[i].item()
            )

            end_sample = int(
                region_edges[i + 1].item()
            )

            contribution = float(
                fake_shap[i]
            )

            regions.append(
                {
                    "start": round(
                        start_sample / float(sr),
                        2,
                    ),

                    "end": round(
                        end_sample / float(sr),
                        2,
                    ),

                    "contribution": contribution,

                    "importance": abs(
                        contribution
                    ),

                    "direction": (
                        "towards AI-generated"
                        if contribution > 0
                        else "towards real"
                    ),
                }
            )

        # ------------------------------------------------------------
        # Strongest regions first.
        # ------------------------------------------------------------

        regions.sort(
            key=lambda r: r["importance"],
            reverse=True,
        )

        selected = regions[:top]

        # Display them chronologically.
        selected.sort(
            key=lambda r: r["start"]
        )

        return {
            "top_regions": selected,

            "all_regions": regions,

            "windows_explained": 1,

            "representative_window": representative_index,
        }

    except Exception:
        logger.exception(
            "Forensics 0.3B SHAP explanation failed"
        )

        return None

# ---------------------------------------------------------------------------
# Forensics narrative
# ---------------------------------------------------------------------------

def forensics_narrative(
    shap_result,
    fake_prob,
    threshold,
):
    if not shap_result:
        return None

    regions = shap_result.get("top_regions") or []

    if not regions:
        return None

    threshold = (
        0.5
        if threshold is None
        else threshold
    )

    fake = (
        fake_prob is not None
        and fake_prob >= threshold
    )

    spans = ", ".join(
        "%.1f-%.1f s" % (
            r["start"],
            r["end"]
        )
        for r in regions[:3]
    )

    fake_regions = [
        r for r in regions
        if r["direction"]
        == "towards AI-generated"
    ]

    real_regions = [
        r for r in regions
        if r["direction"]
        == "towards real"
    ]

    if fake and fake_regions:
        return (
            "The prediction was strongly influenced by "
            "the highlighted audio regions, particularly "
            "around %s. These regions contributed toward "
            "the AI-generated classification."
            % spans
        )

    if not fake and real_regions:
        return (
            "The highlighted audio regions contributed more "
            "toward the real-voice classification, particularly "
            "around %s, while regions associated with the "
            "alternative class had lower influence."
            % spans
        )

    return (
        "The highlighted audio regions around %s had the "
        "strongest influence on the model's prediction."
        % spans
    )


# ---------------------------------------------------------------------------
# Log-mel spectrogram
# ---------------------------------------------------------------------------

def log_mel_png(
    path,
    max_seconds=30.0,
):
    """
    -> base64 PNG of the log-mel spectrogram, or None.

    The spectrogram is generated server-side so that the dashboard and
    forensic report can use the same representation.
    """

    try:
        import matplotlib

        matplotlib.use(
            "Agg"
        )

        import matplotlib.pyplot as plt
        import librosa
        import librosa.display

        from .audio_preprocessing import _decode

        # Use the normal audio decoder rather than train_detector._load_audio.
        # The latter caps the audio at the detector's model window.
        audio, sr = _decode(
            str(path),
            16000,
        )

        if audio.size == 0:
            return None

        audio = audio[
            :int(
                max_seconds * sr
            )
        ]

        mel = librosa.feature.melspectrogram(
            y=audio,
            sr=sr,
            n_mels=96,
            n_fft=1024,
            hop_length=256,
        )

        db = librosa.power_to_db(
            mel,
            ref=np.max,
        )

        fig, ax = plt.subplots(
            figsize=(7.2, 2.6),
            dpi=130,
        )

        img = librosa.display.specshow(
            db,
            sr=sr,
            hop_length=256,
            x_axis="time",
            y_axis="mel",
            ax=ax,
            cmap="magma",
        )

        ax.set_title(
            "Log-mel spectrogram",
            fontsize=9,
        )

        ax.tick_params(
            labelsize=7
        )

        fig.colorbar(
            img,
            ax=ax,
            format="%+2.0f dB",
        ).ax.tick_params(
            labelsize=6
        )

        fig.tight_layout()

        buf = io.BytesIO()

        fig.savefig(
            buf,
            format="png",
        )

        plt.close(fig)

        return base64.b64encode(
            buf.getvalue()
        ).decode("ascii")

    except Exception:
        logger.exception(
            "spectrogram failed"
        )

        return None


# ---------------------------------------------------------------------------
# Narrative for the old scikit-learn detector
# ---------------------------------------------------------------------------

def narrative(
    windows,
    tops,
    fake_prob,
    threshold,
):
    """
    -> readable explanation for the scikit-learn detector.
    """

    fake = (
        fake_prob is not None
        and fake_prob >= (
            threshold or 0.5
        )
    )

    bands = []

    for name, _ in (
        tops or []
    )[:4]:

        band = _band_of(
            name
        )

        if band not in bands:
            bands.append(
                band
            )

    where = ""

    if windows:

        spans = ", ".join(
            "%.1f-%.1f s"
            % (
                a,
                b,
            )
            for a, b, _ in windows
        )

        where = (
            " Evidence is concentrated at %s."
            % spans
        )

    bands = bands[:2]

    if not bands:
        band_text = ""

    elif len(bands) == 1:
        band_text = (
            " mainly in the %s"
            % bands[0]
        )

    else:
        band_text = (
            " in the %s and %s"
            % (
                bands[0],
                bands[1],
            )
        )

    if fake:

        return (
            "The prediction was influenced by several "
            "time-frequency regions%s, giving a high "
            "AI-generated confidence.%s"
            % (
                band_text,
                where,
            )
        )

    return (
        "The highlighted time-frequency regions "
        "contributed toward the real-voice "
        "classification%s, while regions associated "
        "with the synthetic class had lower influence.%s"
        % (
            band_text,
            where,
        )
    )


# ---------------------------------------------------------------------------
# Rule-engine explanations
# ---------------------------------------------------------------------------

def explain_rules(
    details,
    kind,
):
    """
    Attribution for rule-scored message or link.
    """

    d = details or {}

    matched = (
        d.get("matched")
        or []
    )

    contributions = []

    for m in matched:

        if isinstance(
            m,
            dict,
        ):

            label = str(
                m.get(
                    "label",
                    "?",
                )
            )

            pts = m.get(
                "points"
            )

            contributions.append(
                (
                    label,
                    float(pts)
                    if pts is not None
                    else 1.0,
                )
            )

        else:

            contributions.append(
                (
                    str(m),
                    1.0,
                )
            )

    if not contributions:

        for reason in (
            d.get("reasons")
            or []
        )[:6]:

            contributions.append(
                (
                    str(reason),
                    1.0,
                )
            )

    contributions.sort(
        key=lambda p: abs(p[1]),
        reverse=True,
    )

    level = str(
        d.get("level")
        or d.get("rule_level")
        or "low"
    ).lower()

    flagged = level in (
        "high",
        "medium",
    )

    live = (
        d.get("live")
        or {}
    )

    site = []

    if live.get("checked"):

        if live.get("final_url"):
            site.append(
                "resolved to %s"
                % live["final_url"]
            )

        if live.get("status"):
            site.append(
                "server answered HTTP %s"
                % live["status"]
            )

        if live.get("title"):
            site.append(
                "page title: %s"
                % live["title"]
            )

        if live.get(
            "has_password_field"
        ):
            site.append(
                "the page asks for a password"
            )

        elif live.get(
            "has_form"
        ):
            site.append(
                "the page contains a form"
            )

    total = (
        sum(
            abs(v)
            for _, v in contributions
        )
        or 1.0
    )

    if kind == "url":

        subject = "link"

        verdict_word = (
            "malicious"
            if flagged
            else "benign"
        )

    else:

        subject = "message"

        verdict_word = (
            "a scam"
            if flagged
            else "legitimate"
        )

    if contributions:

        top = contributions[0][0]

        narrative_text = (
            'The %s was judged %s mainly because of "%s"%s.'
            % (
                subject,
                verdict_word,
                top,
                (
                    " and %d other indicator%s"
                    % (
                        len(contributions) - 1,
                        ""
                        if len(contributions) == 2
                        else "s",
                    )
                )
                if len(contributions) > 1
                else "",
            )
        )

    else:

        narrative_text = (
            "No indicators matched, so the %s was left "
            "unflagged. Absence of evidence is not proof "
            "of safety."
            % subject
        )

    if site:

        narrative_text += (
            " The link was visited: "
            + "; ".join(site)
            + "."
        )

    return {
        "available": True,

        "kind": kind,

        "model": (
            d.get("model_name")
            or "rule engine"
        ),

        "confidence": round(
            min(
                100.0,
                total,
            ),
            1,
        )
        if contributions
        else 0.0,

        "verdict": (
            "flagged"
            if flagged
            else "clean"
        ),

        "level": level,

        "top_features": [
            {
                "name": name,

                "contribution": round(
                    pts,
                    3,
                ),

                "share": round(
                    abs(pts)
                    / total
                    * 100,
                    1,
                ),

                "direction": (
                    "towards %s"
                    % verdict_word
                ),

                "band": "rule",
            }

            for name, pts
            in contributions[:6]
        ],

        "site": site,

        "narrative": narrative_text,
    }


# ---------------------------------------------------------------------------
# Main explanation dispatcher
# ---------------------------------------------------------------------------

def explain_clip(
    path,
    model_name=None,
):
    """
    -> explanation dictionary.

    Forensics 0.3B:
        Uses SHAP GradientExplainer on the raw waveform.

    Scikit-learn:
        Uses TreeExplainer on the 85-dimensional feature vector.

    Never raises. XAI failure must never break audio analysis.
    """

    from . import model_training

    try:

        from . import forensics_model

        # ---------------------------------------------------------------
        # ACTUAL FORENSICS 0.3B MODEL
        # ---------------------------------------------------------------

        if model_name == forensics_model.NAME:

            result = forensics_model.predict(
                path
            )

            if not result:

                return {
                    "available": False,

                    "reason": (
                        "Forensics 0.3B prediction "
                        "was not available."
                    ),
                }

            fake_prob = float(
                result.get(
                    "fake_probability",
                    0.0,
                )
            )

            threshold = float(
                result.get(
                    "threshold",
                    forensics_model.THRESHOLD,
                )
            )

            shap_result = shap_forensics_clip(
                path
            )

            if not shap_result:

                return {
                    "available": False,

                    "reason": (
                        "SHAP explanation could not "
                        "be generated for the "
                        "Forensics 0.3B waveform model."
                    ),
                }

            regions = (
                shap_result.get(
                    "top_regions"
                )
                or []
            )

            return {
                "available": True,

                "model": forensics_model.NAME,

                "fake_probability": round(
                    fake_prob,
                    4,
                ),

                "threshold": round(
                    threshold,
                    4,
                ),

                "confidence": round(
                    abs(
                        fake_prob
                        - threshold
                    )
                    * 100,
                    1,
                ),

                "verdict": result.get(
                    "verdict",
                    "real",
                ),

                "top_features": [
                    {
                        "name": (
                            "Audio region %.1f-%.1f seconds"
                            % (
                                r["start"],
                                r["end"],
                            )
                        ),

                        "contribution": round(
                            r["contribution"],
                            5,
                        ),

                        "direction": r[
                            "direction"
                        ],

                        "band": "waveform",
                    }

                    for r in regions
                ],

                "windows": [
                    {
                        "start": r[
                            "start"
                        ],

                        "end": r[
                            "end"
                        ],

                        "contribution": round(
                            r[
                                "contribution"
                            ],
                            5,
                        ),

                        "direction": r[
                            "direction"
                        ],
                    }

                    for r in regions
                ],

                "narrative": (
                    forensics_narrative(
                        shap_result,
                        fake_prob,
                        threshold,
                    )
                ),
            }

        # ---------------------------------------------------------------
        # EXISTING SCIKIT-LEARN MODEL
        # ---------------------------------------------------------------

        chosen = (
            model_training._resolve(
                model_name
            )
            if model_name
            else model_training.active_path()
        )

        if not chosen:

            return {
                "available": False,

                "reason": (
                    "No trained model selected."
                ),
            }

        bundle = model_training._load(
            str(chosen),
            chosen.stat().st_mtime,
        )

        if not bundle:

            return {
                "available": False,

                "reason": (
                    "That model could not load."
                ),
            }

        model = bundle["model"]

        threshold = bundle.get(
            "threshold",
            0.5,
        )

        import sys

        from django.conf import settings

        sys.path.insert(
            0,
            str(settings.BASE_DIR),
        )

        from train_detector import features

        x = features(
            path
        )[None, :]

        fake_prob = float(
            model.predict_proba(
                x
            )[0, 1]
        )

        tops = shap_top_features(
            x,
            model,
        )

        windows = contributing_windows(
            path,
            model,
            threshold,
        )

        return {
            "available": True,

            "model": chosen.name,

            "fake_probability": round(
                fake_prob,
                4,
            ),

            "threshold": round(
                threshold,
                4,
            ),

            "confidence": round(
                abs(
                    fake_prob
                    - 0.5
                )
                * 2
                * 100,
                1,
            ),

            "verdict": (
                "fake"
                if fake_prob >= threshold
                else "real"
            ),

            "top_features": [
                {
                    "name": n,

                    "contribution": round(
                        v,
                        5,
                    ),

                    "direction": (
                        "towards AI-generated"
                        if v > 0
                        else "towards real"
                    ),

                    "band": _band_of(n),
                }

                for n, v in (
                    tops or []
                )
            ],

            "windows": [
                {
                    "start": a,
                    "end": b,
                    "fake_probability": p,
                }

                for a, b, p in windows
            ],

            "narrative": narrative(
                windows,
                tops,
                fake_prob,
                threshold,
            ),
        }

    except Exception as exc:

        logger.exception(
            "explain_clip failed"
        )

        return {
            "available": False,

            "reason": str(exc),
        }