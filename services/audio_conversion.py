"""Audio normalization for reotoi.

The browser may upload WAV, MP3, M4A/AAC, OGG/Opus, or WebM audio. Acoustic
analysis uses a single internal format: mono, 16-bit PCM WAV.

The service prefers an explicitly configured/system FFmpeg executable. If one
is not available, it can use the bundled executable exposed by imageio-ffmpeg.
WAV input is handled with soundfile directly when possible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
SUPPORTED_SUFFIXES = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".aac",
    ".ogg",
    ".opus",
    ".webm",
}


def _find_ffmpeg() -> str | None:
    """Find FFmpeg from an environment variable, PATH, or imageio-ffmpeg."""
    configured = os.getenv("FFMPEG_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file() and os.access(configured_path, os.X_OK):
            return str(configured_path)
        raise RuntimeError("FFMPEG_PATH is set but the FFmpeg executable could not be found.")

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        return None


def _temporary_output_path() -> str:
    """Create a unique filename for normalized WAV output."""
    handle = tempfile.NamedTemporaryFile(prefix="reotoi-normalized-", suffix=".wav", delete=False)
    handle.close()
    return handle.name


def _normalize_wav(input_path: Path, output_path: Path) -> None:
    """Convert a readable WAV file to mono 16-bit PCM at the target sample rate."""
    try:
        audio, sample_rate = sf.read(str(input_path), always_2d=False, dtype="float32")
    except (RuntimeError, OSError, ValueError) as exc:
        raise RuntimeError("The WAV file could not be decoded.") from exc

    if audio.size == 0:
        raise ValueError("The audio file is empty.")

    audio_array = np.asarray(audio, dtype=np.float32)
    if audio_array.ndim == 2:
        audio_array = np.mean(audio_array, axis=1)

    if sample_rate != TARGET_SAMPLE_RATE:
        # Let FFmpeg do resampling when available. This branch is only used for
        # WAV input in environments where a native WAV read is possible.
        ffmpeg = _find_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError(
                "Audio conversion is unavailable. FFmpeg is required to resample this recording."
            )
        _run_ffmpeg(ffmpeg, input_path, output_path)
        return

    sf.write(str(output_path), audio_array, TARGET_SAMPLE_RATE, subtype="PCM_16", format="WAV")


def _run_ffmpeg(ffmpeg: str, input_path: Path, output_path: Path) -> None:
    """Decode and normalize audio through FFmpeg."""
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        str(TARGET_CHANNELS),
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg could not be started.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Audio conversion timed out. Please try a shorter recording.") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown FFmpeg error"
        raise ValueError(f"The audio file could not be decoded: {detail}")


def normalize_audio(input_path: str | Path, output_path: str | Path | None = None) -> str:
    """Normalize an uploaded recording into a mono 16-bit PCM WAV file.

    Parameters
    ----------
    input_path:
        Path to the original uploaded recording.
    output_path:
        Optional destination path. When omitted, a temporary WAV is created.

    Returns
    -------
    str
        Path to the normalized WAV file. The caller owns the returned file and
        should delete it after analysis.
    """
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("The submitted audio file could not be found.")

    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            "Unsupported audio format. Please use WAV, MP3, M4A, OGG, AAC, MP4, or WebM."
        )

    destination = Path(output_path).expanduser() if output_path else Path(_temporary_output_path())
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        if suffix == ".wav":
            _normalize_wav(source, destination)
        else:
            ffmpeg = _find_ffmpeg()
            if ffmpeg is None:
                raise RuntimeError(
                    "Audio conversion is unavailable. FFmpeg is required to process this audio format."
                )
            _run_ffmpeg(ffmpeg, source, destination)

        if not destination.is_file() or destination.stat().st_size <= 44:
            raise ValueError("The audio file could not be converted into a usable WAV recording.")

        return str(destination)
    except Exception:
        if not output_path:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
