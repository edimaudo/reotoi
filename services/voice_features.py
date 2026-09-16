"""Acoustic feature extraction for reotoi.

This module deliberately does not use classes. It converts a decoded audio file
into normalized voice characteristics that are suitable for visual mapping.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

MAX_RECORDING_SECONDS = 30


def safe_normalize(value: float, low: float, high: float) -> float:
    """Normalize a numeric value into the 0–1 range."""
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _estimate_pause_ratio(rms: np.ndarray) -> float:
    """Estimate the proportion of low-energy frames."""
    if rms.size == 0:
        return 0.0
    peak = float(rms.max())
    threshold = max(peak * 0.20, 1e-7)
    return float(np.clip((rms < threshold).mean(), 0.0, 1.0))


def analyze_audio(file_path: str | Path) -> dict[str, float]:
    """Extract deterministic acoustic characteristics from an audio file."""
    audio, sample_rate = librosa.load(str(file_path), sr=None, mono=True)

    if audio.size == 0:
        raise ValueError("The audio recording is empty.")

    duration = float(librosa.get_duration(y=audio, sr=sample_rate))
    if duration > MAX_RECORDING_SECONDS + 0.25:
        raise ValueError(f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.")

    rms = librosa.feature.rms(y=audio)[0]
    rms_mean = float(rms.mean()) if rms.size else 0.0

    try:
        f0 = librosa.yin(audio, fmin=70, fmax=400, sr=sample_rate)
        voiced_f0 = f0[np.isfinite(f0) & (f0 >= 70) & (f0 <= 400)]
    except Exception:
        voiced_f0 = np.array([], dtype=float)

    pitch_median = float(np.median(voiced_f0)) if voiced_f0.size else 150.0
    pitch_spread = float(np.std(voiced_f0)) if voiced_f0.size else 0.0

    onset_env = librosa.onset.onset_strength(y=audio, sr=sample_rate)
    onset_mean = float(onset_env.mean()) if onset_env.size else 0.0

    spectral_centroid = librosa.feature.spectral_centroid(
        y=audio,
        sr=sample_rate,
    )[0]
    centroid_mean = (
        float(spectral_centroid.mean()) if spectral_centroid.size else 0.0
    )

    return {
        "pitch": safe_normalize(pitch_median, 80.0, 300.0),
        "energy": safe_normalize(rms_mean, 0.005, 0.15),
        "rhythm": safe_normalize(onset_mean, 0.1, 3.0),
        "variation": safe_normalize(pitch_spread, 5.0, 80.0),
        "pause": _estimate_pause_ratio(rms),
        "spectral": safe_normalize(centroid_mean, 500.0, 4000.0),
        "speech_rate": safe_normalize(onset_mean, 0.1, 4.0),
        "duration": min(duration, MAX_RECORDING_SECONDS),
    }


def calculate_voice_dna(features: dict[str, float]) -> dict[str, int]:
    """Convert internal normalized features into 0–10 UI values."""
    keys = ("pitch", "energy", "rhythm", "variation", "pause")
    return {
        key: int(round(float(np.clip(features.get(key, 0.0), 0.0, 1.0)) * 10))
        for key in keys
    }
