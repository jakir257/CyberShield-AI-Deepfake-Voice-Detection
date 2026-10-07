import os

import librosa
import numpy as np
import soundfile as sf


# =========================================================
# SETTINGS
# =========================================================

SAMPLE_RATE = 16000

# Standard segment duration
SEGMENT_DURATION = 4  # seconds

# Silence threshold
TOP_DB = 30


# =========================================================
# SILENCE TRIMMING
# =========================================================

# Internal silence longer than this will be removed
MAX_INTERNAL_SILENCE = 1.0  # seconds


def trim_silence(audio, sample_rate=SAMPLE_RATE):           #Added
    """
    Remove unwanted silence while preserving short natural pauses.

    - Removes silence at the beginning.
    - Removes silence at the end.
    - Preserves short pauses between speech.
    - Removes internal silence longer than MAX_INTERNAL_SILENCE.
    """

    if audio is None or len(audio) == 0:
        raise ValueError("Audio is empty.")

    # Detect non-silent regions
    intervals = librosa.effects.split(
        audio,
        top_db=TOP_DB,
        frame_length=1024,
        hop_length=256
    )

    if len(intervals) == 0:
        raise ValueError(
            "Audio contains no usable speech."
        )

    # -------------------------------------------------
    # Remove silence from beginning and end
    # -------------------------------------------------

    first_start = intervals[0][0]
    last_end = intervals[-1][1]

    audio = audio[first_start:last_end]

    # Recalculate intervals because audio was trimmed
    intervals = librosa.effects.split(
        audio,
        top_db=TOP_DB,
        frame_length=1024,
        hop_length=256
    )

    if len(intervals) == 0:
        raise ValueError(
            "Audio contains no usable speech after trimming."
        )

    # -------------------------------------------------
    # Keep speech + short pauses
    # Remove only long internal silence
    # -------------------------------------------------

    output_parts = []

    for i in range(len(intervals)):

        start = intervals[i][0]
        end = intervals[i][1]

        # Add current speech segment
        output_parts.append(audio[start:end])

        # Check silence between this segment
        # and the next speech segment
        if i < len(intervals) - 1:

            next_start = intervals[i + 1][0]

            silence_start = end
            silence_end = next_start

            silence_length = (
                silence_end - silence_start
            ) / sample_rate

            # Keep short natural pause
            if silence_length <= MAX_INTERNAL_SILENCE:

                output_parts.append(
                    audio[silence_start:silence_end]
                )

            # Otherwise, don't append it.
            # This removes the long silence.

    # Combine everything
    trimmed_audio = np.concatenate(output_parts)

    if len(trimmed_audio) == 0:
        raise ValueError(
            "Audio contains no usable speech after trimming."
        )

    return trimmed_audio.astype(np.float32)


# =========================================================
# AUDIO SEGMENTATION
# =========================================================

def split_audio_into_segments(
    audio,
    sample_rate=SAMPLE_RATE,
    segment_duration=SEGMENT_DURATION
):
    """
    Splits audio into fixed-duration segments.

    Each segment is standardized to the same duration.
    The final segment is zero-padded if necessary.
    """

    if audio is None or len(audio) == 0:
        raise ValueError("Audio is empty.")

    segment_length = int(
        sample_rate * segment_duration
    )

    segments = []

    for start in range(
        0,
        len(audio),
        segment_length
    ):

        end = start + segment_length

        segment = audio[start:end]

        # Ignore completely empty segments
        if len(segment) == 0:
            continue

        # Pad final segment with silence
        if len(segment) < segment_length:

            padding_length = (
                segment_length - len(segment)
            )

            segment = np.pad(
                segment,
                (0, padding_length),
                mode="constant"
            )

        segments.append(segment.astype(np.float32))

    if len(segments) == 0:
        raise ValueError(
            "No usable audio segments were created."
        )

    return segments


# =========================================================
# COMPLETE SEGMENTATION PIPELINE
# =========================================================

def process_audio_segments(
    input_path,
    output_directory,
    sample_rate=SAMPLE_RATE,
    segment_duration=SEGMENT_DURATION
):
    """
    Complete pipeline:

    1. Load standardized WAV
    2. Validate audio
    3. Trim beginning/end silence
    4. Save trimmed audio
    5. Split into fixed-duration segments
    6. Save each segment as WAV
    """

    # -----------------------------------------------------
    # Create output directory
    # -----------------------------------------------------

    os.makedirs(
        output_directory,
        exist_ok=True
    )


    # -----------------------------------------------------
    # Load audio
    # -----------------------------------------------------

    try:

        audio, sr = librosa.load(
            input_path,
            sr=sample_rate,
            mono=True
        )

    except Exception as e:

        raise ValueError(
            f"Unable to load audio: {str(e)}"
        )


    # -----------------------------------------------------
    # Check empty audio
    # -----------------------------------------------------

    if audio is None or len(audio) == 0:

        raise ValueError(
            "The audio file is empty."
        )


    # -----------------------------------------------------
    # Check minimum duration
    # -----------------------------------------------------

    original_duration = (
        len(audio) / sample_rate
    )

    if original_duration < 0.5:

        raise ValueError(
            "Audio is too short for segmentation."
        )


    # -----------------------------------------------------
    # Trim silence
    # -----------------------------------------------------

    trimmed_audio = trim_silence(
        audio,
        sample_rate
    )


    # -----------------------------------------------------
    # Duration after trimming
    # -----------------------------------------------------

    trimmed_duration = (
        len(trimmed_audio) / sample_rate
    )


    # -----------------------------------------------------
    # Split into segments
    # -----------------------------------------------------

    segments = split_audio_into_segments(
        trimmed_audio,
        sample_rate,
        segment_duration
    )

    # -----------------------------------------------------
    # SAVE TRIMMED AUDIO
    # -----------------------------------------------------

    trimmed_filename = "trimmed_audio.wav"

    trimmed_path = os.path.join(
        output_directory,
        trimmed_filename
    )

    sf.write(
        trimmed_path,
        trimmed_audio,
        sample_rate,
        subtype="PCM_16"
    )

    # -----------------------------------------------------
    # Save segments
    # -----------------------------------------------------

    segment_files = []

    for index, segment in enumerate(
        segments,
        start=1
    ):

        filename = (
            f"segment_{index:03d}.wav"
        )

        output_path = os.path.join(
            output_directory,
            filename
        )

        sf.write(
            output_path,
            segment,
            sample_rate,
            subtype="PCM_16"
        )

        segment_files.append(
            output_path
        )


    # -----------------------------------------------------
    # Return processing information
    # -----------------------------------------------------

    return {
        "original_duration": round(
            original_duration,
            2
        ),

        "trimmed_duration": round(
            trimmed_duration,
            2
        ),

        "segment_duration": segment_duration,

        "segment_count": len(
            segment_files
        ),

        "trimmed_path":
            trimmed_path,

        "segment_files": segment_files
    }