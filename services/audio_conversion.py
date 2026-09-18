"""Audio decoding and normalization for reotoi.

The web app accepts microphone audio and user-supplied audio/media files. The
server decodes formats supported by the installed libsndfile build, including
WAV, MP3, OGG and FLAC. Browser-normalized WAV is also accepted for containers
or codecs that libsndfile cannot decode, such as supported MP4/M4A/WebM input.

Format detection is based on the actual uploaded bytes, not the filename or
MIME type. This prevents valid MP3/OGG/etc. files from being rejected because a
browser supplied an unexpected filename or content type.

No external codec runtime is used.
"""

from __future__ import annotations

from io import BytesIO
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


def _read_audio(source: Path) -> tuple[np.ndarray, int, str]:
    """Decode supported audio from the actual file bytes.

    Using BytesIO prevents libsndfile from relying on the temporary filename
    extension when identifying formats. A file named .bin can therefore still
    be decoded correctly when its contents are a valid MP3, OGG, FLAC or WAV.
    """
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ValueError(
            "reotoi could not read this audio file. Please try another supported file."
        ) from exc

    if not raw:
        raise ValueError("The audio recording is empty.")

    buffer = BytesIO(raw)

    try:
        info = sf.info(buffer)
    except (RuntimeError, OSError) as exc:
        raise ValueError(
            "reotoi could not decode this audio file. Use MP3, OGG, FLAC or WAV, "
            "or provide a browser-supported MP4, M4A or WebM file."
        ) from exc

    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("The audio recording is empty or has an invalid sample rate.")

    try:
        buffer.seek(0)
        audio, sample_rate = sf.read(
            buffer,
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

    detected_format = (info.format or "audio").upper()
    return audio, int(sample_rate), detected_format


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
    audio = np.nan_to_num(
        audio,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )

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
    """Decode audio and produce normalized mono 16 kHz PCM WAV.

    Supported source formats depend on the installed libsndfile build. The
    format is detected from the file contents rather than its suffix, so
    correct MP3/OGG/FLAC/WAV bytes remain decodable even if upload metadata is
    missing or incorrect.
    """
    source = Path(input_path).expanduser().resolve()

    if not source.is_file():
        raise ValueError("The submitted audio file could not be found.")

    audio, sample_rate, _detected_format = _read_audio(source)

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
            raise ValueError("The audio could not be normalized for analysis.")

        # Validate the generated WAV independently of the source format.
        try:
            normalized_info = sf.info(str(destination))
        except (RuntimeError, OSError) as exc:
            raise ValueError(
                "The normalized audio could not be read for analysis."
            ) from exc

        if (
            normalized_info.frames <= 0
            or normalized_info.samplerate != TARGET_SAMPLE_RATE
            or normalized_info.channels != 1
        ):
            raise ValueError(
                "The normalized audio did not produce the expected mono 16 kHz format."
            )

        return str(destination)
    except Exception:
        if not destination_was_provided:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
