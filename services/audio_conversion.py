"""Audio decoding and normalization for reotoi.

User uploads may be WAV, MP3, OGG, FLAC and other formats supported by the installed libsndfile build. Formats such as
MP4/M4A/WebM/AAC that are not handled by libsndfile are normalized in the
browser before reaching this service.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

TARGET_SAMPLE_RATE = 16_000

# Formats expected to arrive directly from the browser and be decoded by
# libsndfile. MP3 support requires libsndfile >= 1.1.0.
DIRECT_INPUT_SUFFIXES = {
    ".wav",
    ".wave",
    ".flac",
    ".mp3",
    ".ogg",
    ".oga",
    ".aif",
    ".aiff",
    ".au",
    ".snd",
}


def _temporary_wav_path() -> str:
    """Return a unique path for normalized WAV output."""
    handle = tempfile.NamedTemporaryFile(
        prefix="reotoi-normalized-",
        suffix=".wav",
        delete=False,
    )
    handle.close()
    return handle.name


def _read_audio(source: Path) -> tuple[np.ndarray, int]:
    """Decode an input audio file through libsndfile."""
    suffix = source.suffix.lower()

    if suffix not in DIRECT_INPUT_SUFFIXES:
        raise ValueError(
            "This audio format requires browser-side audio conversion before analysis. "
            "Use MP3, OGG, FLAC or WAV, or choose a browser-supported MP4/M4A/WebM file."
        )

    try:
        info = sf.info(str(source))
    except (RuntimeError, OSError) as exc:
        raise ValueError(
            "reotoi could not read this audio file. Please try another MP3, OGG, FLAC or WAV file."
        ) from exc

    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("The audio recording is empty or has an invalid sample rate.")

    try:
        audio, sample_rate = sf.read(
            str(source),
            always_2d=False,
            dtype="float32",
        )
    except (RuntimeError, OSError) as exc:
        raise ValueError(
            "reotoi could not decode this audio file. Please try another supported file."
        ) from exc

    audio = np.asarray(audio, dtype=np.float32)

    if audio.size == 0:
        raise ValueError("The audio recording is empty.")

    return audio, int(sample_rate)


def _write_normalized_wav(
    audio: np.ndarray,
    sample_rate: int,
    output_path: Path,
) -> None:
    """Downmix, resample and write mono 16-bit PCM WAV."""
    if audio.ndim == 2:
        audio = np.mean(audio, axis=1)
    elif audio.ndim != 1:
        raise ValueError("The audio recording has an unsupported channel layout.")

    audio = np.asarray(audio, dtype=np.float32)

    if audio.size == 0:
        raise ValueError("The audio recording is empty.")

    if sample_rate <= 0:
        raise ValueError("The audio recording has an invalid sample rate.")

    if sample_rate != TARGET_SAMPLE_RATE:
        audio = librosa.resample(
            audio,
            orig_sr=sample_rate,
            target_sr=TARGET_SAMPLE_RATE,
        )
        sample_rate = TARGET_SAMPLE_RATE

    if audio.size == 0:
        raise ValueError("The audio recording is empty after normalization.")

    # Prevent NaN/Inf values from reaching feature extraction.
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

    sf.write(
        str(output_path),
        audio,
        sample_rate,
        format="WAV",
        subtype="PCM_16",
    )


def normalize_audio(
    input_path: str | Path,
    output_path: str | Path | None = None,
    max_duration_seconds: float | None = None,
) -> str:
    """Decode supported input audio and produce normalized mono 16 kHz WAV.

    The input may be a supported source format such as MP3, OGG, FLAC or WAV.
    MP4/M4A/WebM/AAC are expected to have been converted to WAV by the browser
    before this function is called.
    """
    source = Path(input_path).expanduser().resolve()

    if not source.is_file():
        raise ValueError("The submitted audio file could not be found.")

    audio, sample_rate = _read_audio(source)

    if max_duration_seconds is not None:
        duration = audio.shape[0] / sample_rate
        if duration > max_duration_seconds + 0.25:
            raise ValueError(
                f"Audio must be {max_duration_seconds:g} seconds or less."
            )

    destination_was_provided = output_path is not None
    destination = (
        Path(output_path).expanduser().resolve()
        if output_path
        else Path(_temporary_wav_path()).resolve()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        _write_normalized_wav(
            audio,
            sample_rate,
            destination,
        )

        if not destination.is_file() or destination.stat().st_size <= 44:
            raise ValueError(
                "The audio could not be normalized for analysis."
            )

        return str(destination)
    except Exception:
        if not destination_was_provided:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
