"""AssemblyAI integration for speech-level analysis."""

from __future__ import annotations

import os
from typing import Any

import assemblyai as aai


def get_api_key() -> str:
    """Return the AssemblyAI API key or raise a configuration error."""
    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is not configured.")
    return api_key


def analyze_speech(file_path: str) -> dict[str, Any]:
    """Transcribe the recording and return only speech context needed by reotoi."""
    aai.settings.api_key = get_api_key()

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(file_path)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(transcript.error or "AssemblyAI could not process the recording.")

    return {
        "transcript": getattr(transcript, "text", None),
        "duration_seconds": float(getattr(transcript, "audio_duration", None) or 0.0),
    }
