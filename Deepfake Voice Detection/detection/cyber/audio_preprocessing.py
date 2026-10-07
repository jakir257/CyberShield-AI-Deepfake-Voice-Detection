import os
import librosa
import soundfile as sf
import numpy as np

# Standard Settings
TARGET_SR = 16000
MIN_DURATION = 2  # seconds

SUPPORTED_FORMATS = [".wav", ".mp3", ".m4a", ".flac"]


def _decode(path, target_sr):
    """-> (mono float32 at target_sr, target_sr). No ffmpeg needed.

    librosa/soundfile first; pyav when that raises. Same fallback pair as
    train_detector.py, so an upload and a training clip are decoded the same
    way. librosa alone is not enough: libsndfile refuses some flac, and
    without a backend it silently falls through to audioread.
    """
    try:
        audio, _ = librosa.load(path, sr=target_sr, mono=True)
        if audio.size:
            return audio.astype(np.float32), target_sr
    except Exception:
        pass

    import av

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="flt", layout="mono", rate=target_sr)
        chunks = []
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().ravel())

    if not chunks:
        raise ValueError("no audio could be decoded from %s" % path)
    return np.concatenate(chunks).astype(np.float32), target_sr


class AudioPreprocessor:

    def __init__(self,
                 target_sr=TARGET_SR,
                 min_duration=MIN_DURATION):
        self.target_sr = target_sr
        self.min_duration = min_duration

    def validate_extension(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()

        if ext not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format: {ext}"
            )

    def convert_to_wav(self, input_path, output_path):
        """
        Convert MP3/M4A/FLAC/WAV to a 16 kHz mono WAV.

        Decoded with soundfile, falling back to pyav - not pydub. pydub shells
        out to ffmpeg for anything that is not WAV, and a machine without
        ffmpeg on PATH raises "[WinError 2] The system cannot find the file
        specified" on every mp3/m4a upload. Both libraries here are already
        installed and need no external binary.
        """

        audio, _sr = _decode(input_path, self.target_sr)
        sf.write(output_path, audio, self.target_sr)

        return output_path

    def load_audio(self, filepath):
        """
        Load audio with Librosa
        Resample to 16kHz
        Convert to mono
        """

        try:
            # _decode, not librosa directly: some flac files decode only via
            # pyav, and calling librosa here would report them as corrupted
            return _decode(filepath, self.target_sr)

        except Exception as e:
            raise ValueError(
                f"Corrupted audio file: {e}"
            )

    def check_empty_audio(self, audio):
        """
        Detect silent/empty audio
        """

        if len(audio) == 0:
            raise ValueError(
                "Audio is empty."
            )

        if np.max(np.abs(audio)) < 1e-5:
            raise ValueError(
                "Audio contains only silence."
            )

    def check_short_audio(self, audio, sr):
        duration = len(audio) / sr

        if duration < self.min_duration:
            raise ValueError(
                f"Audio too short. Duration={duration:.2f}s"
            )

    def save_standardized_audio(
        self,
        audio,
        output_path
    ):

        sf.write(
            output_path,
            audio,
            self.target_sr
        )

        return output_path

    def process(self,
                input_path,
                output_path):

        self.validate_extension(input_path)

        temp_wav = output_path

        self.convert_to_wav(
            input_path,
            temp_wav
        )

        audio, sr = self.load_audio(
            temp_wav
        )

        self.check_empty_audio(audio)

        self.check_short_audio(
            audio,
            sr
        )

        self.save_standardized_audio(
            audio,
            output_path
        )

        return {
            "status": "success",
            "sample_rate": sr,
            "duration": len(audio)/sr,
            "path": output_path
        }