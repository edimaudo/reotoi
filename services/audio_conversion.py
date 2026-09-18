"""Validate and normalize browser-prepared PCM WAV audio for reotoi."""

from __future__ import annotations

import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

TARGET_SAMPLE_RATE = 16_000


def _temporary_wav_path() -> str:
    """Return a unique path for normalized WAV output."""
    handle = tempfile.NamedTemporaryFile(
        prefix="reotoi-normalized-",
        suffix=".wav",
        delete=False,
    )
    handle.close()
    return handle.name


def _is_wav(path: Path) -> bool:
    """Check the RIFF/WAVE file signature."""
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False

    return (
        len(header) >= 12
        and header[:4] == b"RIFF"
        and header[8:12] == b"WAVE"
    )


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
) -> str:
    """Validate and standardize the browser-prepared WAV for analysis.

    User-facing input formats are handled before this function is called.
    This function intentionally validates the actual file bytes rather than
    relying on a browser MIME type.
    """
    source = Path(input_path).expanduser().resolve()

    if not source.is_file():
        raise ValueError("The submitted audio file could not be found.")

    if not _is_wav(source):
        raise ValueError(
            "reotoi could not process the normalized audio. Please try the recording or file again."
        )

    try:
        info = sf.info(str(source))
        if info.frames <= 0:
            raise ValueError("The audio recording is empty.")

        audio, sample_rate = sf.read(
            str(source),
            always_2d=False,
            dtype="float32",
        )
    except ValueError:
        raise
    except (RuntimeError, OSError) as exc:
        raise ValueError(
            "reotoi could not decode the normalized audio. Please try the recording or file again."
        ) from exc

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
            int(sample_rate),
            destination,
        )

        if not destination.is_file() or destination.stat().st_size <= 44:
            raise ValueError(
                "The normalized audio could not be prepared for analysis."
            )

        return str(destination)
    except Exception:
        if not destination_was_provided:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
