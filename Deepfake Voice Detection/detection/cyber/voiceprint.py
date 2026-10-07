"""Trusted voice verification - does this call sound like who it claims to be?

A different question from deepfake detection. The detector asks "was this
synthesised"; this asks "is this the same speaker as the enrolled sample". A
scammer using their own genuine voice to impersonate a relative passes the
first test and fails this one.

The voice print is a mean+std MFCC vector, compared by cosine similarity. That
is a deliberately modest method and its limits are stated plainly in
`CAVEAT` - it is text-independent and quick, but it is not a speaker-verification
model, and it is sensitive to channel and noise. A dedicated embedding network
(ECAPA-TDNN, x-vector) would be markedly better; this needs no extra download
and is honest about what it is.
"""

import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

#: Bands for the similarity score. Chosen so "possible match" covers the range
#: where this method genuinely cannot decide, rather than pretending it can.
STRONG = 0.85
POSSIBLE = 0.70

CAVEAT = ("Similarity is measured from MFCC statistics, which capture voice "
          "timbre but are also affected by the phone, the codec and background "
          "noise. Treat a match as corroboration, not identification.")


def embed(path):
    """-> a 40-d voice print (20 MFCC means + 20 stds), or None.

    Mean and standard deviation over time, so the result does not depend on
    what was said or how long the clip is.
    """
    try:
        import librosa
        from .audio_preprocessing import _decode

        audio, sr = _decode(str(path), SAMPLE_RATE)
        if audio.size < sr // 2:            # under half a second is not a voice
            return None

        # trim silence: leading/trailing quiet drags the statistics toward noise
        try:
            intervals = librosa.effects.split(audio, top_db=30)
            if len(intervals):
                speech = np.concatenate([audio[a:b] for a, b in intervals])
                if speech.size >= sr // 2:
                    audio = speech
        except Exception:
            pass

        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20)
        vec = np.concatenate([mfcc.mean(axis=1), mfcc.std(axis=1)])
        return [float(v) for v in vec]
    except Exception:
        logger.exception("voice print failed for %s", path)
        return None


def similarity(a, b):
    """-> 0..1 cosine similarity between two voice prints.

    Each half is standardised first: MFCC means and stds live on very different
    scales, and without that the means alone would decide the answer.
    """
    if not a or not b or len(a) != len(b):
        return None
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)

    half = len(x) // 2
    out = []
    for lo, hi in ((0, half), (half, len(x))):
        xs, ys = x[lo:hi], y[lo:hi]
        scale = np.std(np.concatenate([xs, ys])) or 1.0
        out.append((xs / scale, ys / scale))

    x = np.concatenate([p[0] for p in out])
    y = np.concatenate([p[1] for p in out])

    denom = (np.linalg.norm(x) * np.linalg.norm(y)) or 1.0
    cos = float(np.dot(x, y) / denom)
    return round(max(0.0, min(1.0, (cos + 1) / 2)), 4)   # -1..1 -> 0..1


def verdict_for(score):
    """-> a word for the score, plus what it does and does not mean."""
    if score is None:
        return "No comparison", "The sample could not be compared."
    if score >= STRONG:
        return "Likely match", ("The voice is consistent with the enrolled "
                                "sample.")
    if score >= POSSIBLE:
        return "Possible match", ("Some similarity, but not enough to "
                                  "confirm. Verify by another means.")
    return "No match", ("The voice does not resemble the enrolled sample.")


def compare(path, contacts):
    """Compare one clip against enrolled contacts.

    -> {"best": {...}, "all": [...]} ranked, or {"error": ...}.
    """
    probe = embed(path)
    if probe is None:
        return {"error": "The clip is too short or could not be read."}

    scored = []
    for c in contacts:
        s = similarity(probe, c.embedding)
        if s is None:
            continue
        label, note = verdict_for(s)
        scored.append({
            "id": c.id,
            "name": c.name,
            "relation": c.relation,
            "similarity": round(s * 100, 1),
            "result": label,
            "note": note,
        })

    if not scored:
        return {"error": "No trusted voices are enrolled yet."}

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return {"best": scored[0], "all": scored, "caveat": CAVEAT}
