import os

import librosa
import numpy as np
import soundfile as sf


# =========================================================
# SETTINGS
# =========================================================

SAMPLE_RATE = 16000

# Conservative noise reduction
NOISE_REDUCTION_STRENGTH = 0.5

# FFT settings
N_FFT = 1024
HOP_LENGTH = 256


# =========================================================
# NOISE REDUCTION
# =========================================================

def reduce_noise(
    audio,
    sample_rate=SAMPLE_RATE,
    strength=NOISE_REDUCTION_STRENGTH
):
    """
    Performs conservative spectral noise reduction.

    The first part of the signal is used as an
    approximate noise profile.

    This is intentionally mild so that speech
    characteristics and possible DeepFake artifacts
    are not aggressively removed.
    """

    if audio is None or len(audio) == 0:
        raise ValueError("Audio is empty.")

    # -----------------------------------------------------
    # STFT
    # -----------------------------------------------------

    stft = librosa.stft(
        audio,
        n_fft=N_FFT,
        hop_length=HOP_LENGTH
    )

    magnitude = np.abs(stft)
    phase = np.angle(stft)

    # -----------------------------------------------------
    # Estimate noise from first 0.5 seconds
    # -----------------------------------------------------

    noise_duration = 0.5

    noise_samples = int(
        sample_rate * noise_duration
    )

    noise_samples = min(
        noise_samples,
        len(audio)
    )

    if noise_samples < 100:
        return audio.astype(np.float32)

    noise_audio = audio[:noise_samples]

    noise_stft = librosa.stft(
        noise_audio,
        n_fft=N_FFT,
        hop_length=HOP_LENGTH
    )

    noise_magnitude = np.abs(noise_stft)

    noise_profile = np.mean(
        noise_magnitude,
        axis=1,
        keepdims=True
    )

    # -----------------------------------------------------
    # Limit noise reduction
    # -----------------------------------------------------

    reduction_strength = np.clip(
        strength,
        0.0,
        1.0
    )

    # Never completely remove a frequency component.
    minimum_gain = 0.25

    gain = (
        1.0
        -
        reduction_strength
        *
        (
            noise_profile
            /
            (magnitude + 1e-8)
        )
    )

    gain = np.clip(
        gain,
        minimum_gain,
        1.0
    )

    # -----------------------------------------------------
    # Apply gain
    # -----------------------------------------------------

    cleaned_stft = (
        magnitude
        *
        gain
        *
        np.exp(1j * phase)
    )

    # -----------------------------------------------------
    # Convert back to waveform
    # -----------------------------------------------------

    cleaned_audio = librosa.istft(
        cleaned_stft,
        hop_length=HOP_LENGTH,
        length=len(audio)
    )

    # -----------------------------------------------------
    # Prevent clipping
    # -----------------------------------------------------

    peak = np.max(
        np.abs(cleaned_audio)
    )

    if peak > 0.99:

        cleaned_audio = (
            cleaned_audio / peak
        ) * 0.99

    return cleaned_audio.astype(
        np.float32
    )


# =========================================================
# COMPLETE NOISE REDUCTION PIPELINE
# =========================================================

def process_noise_reduction(
    input_path,
    output_directory,
    strength=NOISE_REDUCTION_STRENGTH
):
    """
    Loads standardized WAV, performs conservative
    noise reduction and saves the result.
    """

    os.makedirs(
        output_directory,
        exist_ok=True
    )

    # -----------------------------------------------------
    # Load audio
    # -----------------------------------------------------

    try:

        audio, sample_rate = librosa.load(
            input_path,
            sr=SAMPLE_RATE,
            mono=True
        )

    except Exception as e:

        raise ValueError(
            f"Unable to load audio: {str(e)}"
        )

    # -----------------------------------------------------
    # Validate
    # -----------------------------------------------------

    if audio is None or len(audio) == 0:

        raise ValueError(
            "Audio is empty."
        )

    # -----------------------------------------------------
    # Apply conservative noise reduction
    # -----------------------------------------------------

    cleaned_audio = reduce_noise(
        audio,
        sample_rate=SAMPLE_RATE,
        strength=strength
    )

    # -----------------------------------------------------
    # Save
    # -----------------------------------------------------

    output_filename = (
        "noise_reduced_audio.wav"
    )

    output_path = os.path.join(
        output_directory,
        output_filename
    )

    sf.write(
        output_path,
        cleaned_audio,
        SAMPLE_RATE,
        subtype="PCM_16"
    )

    if not os.path.exists(output_path):
        raise ValueError(
            "Noise-reduced audio file was not created."
        )

    if os.path.getsize(output_path) == 0:
        raise ValueError(
            "Noise-reduced audio file is empty."
        )

    # -----------------------------------------------------
    # Return information
    # -----------------------------------------------------

    duration = (
        len(cleaned_audio)
        /
        SAMPLE_RATE
    )

    return {

        "output_path":
            output_path,

        "sample_rate":
            SAMPLE_RATE,

        "channels":
            "Mono",

        "duration":
            round(duration, 2),

        "strength":
            strength
    }