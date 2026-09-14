from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

import assemblyai as aai
import librosa
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reotoi")

app = FastAPI(
    title="reotoi · voice art",
    description="Turn characteristics of a voice into unique visual artwork.",
    version="0.3.0",
)

# Static assets are part of the application package and must be present at startup.
# Mounting unconditionally ensures the Jinja `url_for('static', ...)` route always exists.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_RECORDING_SECONDS = 30
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MIN_AUDIO_BYTES = 512
ALLOWED_INPUT_SOURCES = {"microphone", "upload"}
ALLOWED_THEMES = {
    "abstract",
    "nature",
    "cosmos",
    "architecture",
    "organic",
    "geometric",
    "surprise",
}
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/webm;codecs=opus",
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/ogg",
    "audio/opus",
    "audio/aac",
}


# ---------------------------------------------------------------------------
# Configuration and validation
# ---------------------------------------------------------------------------


def validate_theme(theme: str) -> str:
    """Validate and normalize the selected artwork theme."""
    normalized = theme.strip().lower()
    if normalized not in ALLOWED_THEMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid theme.",
        )
    return normalized


def validate_input_source(input_source: str) -> str:
    """Validate whether the audio came from the microphone or an upload."""
    normalized = input_source.strip().lower()
    if normalized not in ALLOWED_INPUT_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid audio input source.",
        )
    return normalized


def validate_audio_metadata(file: UploadFile) -> None:
    """Reject unsupported audio content types before writing to disk."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported audio format. Please use a microphone recording "
                "or a WAV, MP3, M4A, OGG, AAC, MP4, or WebM audio file."
            ),
        )


def extension_for_audio(file: UploadFile) -> str:
    """Return a safe temporary-file extension based on content type/name."""
    content_type = (file.content_type or "").lower()
    content_extensions = {
        "audio/webm": ".webm",
        "audio/webm;codecs=opus": ".webm",
        "audio/wav": ".wav",
        "audio/wave": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/aac": ".aac",
    }

    if content_type in content_extensions:
        return content_extensions[content_type]

    if file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix in {".webm", ".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".aac"}:
            return suffix

    return ".audio"


async def save_upload_to_temp(file: UploadFile) -> tuple[str, int]:
    """Stream an uploaded audio file to a temporary path with size limits."""
    validate_audio_metadata(file)

    total_bytes = 0
    suffix = extension_for_audio(file)
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path = temp_file.name

    try:
        with temp_file:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > MAX_AUDIO_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Audio must be 10 MB or smaller.",
                    )

                temp_file.write(chunk)

        if total_bytes < MIN_AUDIO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The audio recording is empty or too short.",
            )

        return temp_path, total_bytes
    except Exception:
        delete_temp_file(temp_path)
        raise


def delete_temp_file(file_path: str) -> None:
    """Best-effort cleanup for temporary uploaded audio."""
    try:
        os.unlink(file_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Voice analysis
# ---------------------------------------------------------------------------


def safe_normalize(value: float, low: float, high: float) -> float:
    """Normalize a value to the 0–1 range without division errors."""
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def calculate_voice_dna(features: dict[str, float]) -> dict[str, int]:
    """Convert normalized internal features to 0–10 display values."""
    return {
        key: int(round(float(np.clip(value, 0.0, 1.0)) * 10))
        for key, value in features.items()
        if key in {"pitch", "energy", "rhythm", "variation", "pause"}
    }


def analyze_audio(file_path: str) -> dict[str, float]:
    """Extract deterministic acoustic characteristics from the recording."""
    try:
        audio, sample_rate = librosa.load(file_path, sr=None, mono=True)
    except Exception as exc:
        logger.exception("Unable to decode audio", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The audio could not be decoded. Please try another recording or file.",
        ) from exc

    if audio.size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The audio recording is empty.",
        )

    duration = float(librosa.get_duration(y=audio, sr=sample_rate))
    if duration > MAX_RECORDING_SECONDS + 0.25:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.",
        )

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

    spectral_centroid = librosa.feature.spectral_centroid(y=audio, sr=sample_rate)[0]
    centroid_mean = float(spectral_centroid.mean()) if spectral_centroid.size else 0.0

    rms_max = float(rms.max()) if rms.size else 0.0
    threshold = max(rms_max * 0.20, 1e-7)
    pause_ratio = float((rms < threshold).mean()) if rms.size else 0.0

    return {
        "pitch": safe_normalize(pitch_median, 80.0, 300.0),
        "energy": safe_normalize(rms_mean, 0.005, 0.15),
        "rhythm": safe_normalize(onset_mean, 0.1, 3.0),
        "variation": safe_normalize(pitch_spread, 5.0, 80.0),
        "pause": float(np.clip(pause_ratio, 0.0, 1.0)),
        "spectral": safe_normalize(centroid_mean, 500.0, 4000.0),
        "speech_rate": safe_normalize(onset_mean, 0.1, 4.0),
        "duration": min(duration, MAX_RECORDING_SECONDS),
    }


# ---------------------------------------------------------------------------
# AssemblyAI integration
# ---------------------------------------------------------------------------


def get_assemblyai_api_key() -> str:
    """Read the AssemblyAI key from the deployment environment."""
    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice analysis service is not available right now. Please try again later.",
        )
    return api_key


def analyze_speech_with_assemblyai(file_path: str) -> dict[str, Any]:
    """Use AssemblyAI for speech-level context without replacing acoustic analysis."""
    aai.settings.api_key = get_assemblyai_api_key()

    try:
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(file_path)
    except Exception as exc:
        logger.exception("AssemblyAI request failed", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice analysis service is not available right now. Please try again later.",
        ) from exc

    if transcript.status == aai.TranscriptStatus.error:
        logger.error("AssemblyAI transcription failed: %s", transcript.error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice analysis service could not process the recording. Please try again.",
        )

    return {
        "transcript": getattr(transcript, "text", None),
        "duration_seconds": float(getattr(transcript, "audio_duration", None) or 0.0),
    }


# ---------------------------------------------------------------------------
# Artwork generation boundary
# ---------------------------------------------------------------------------


def build_visual_parameters(features: dict[str, float], theme: str) -> dict[str, Any]:
    """Create a deterministic intermediate representation for the art engine."""
    return {
        "theme": theme,
        "voice": {
            "pitch": features["pitch"],
            "energy": features["energy"],
            "rhythm": features["rhythm"],
            "variation": features["variation"],
            "pause": features["pause"],
            "spectral": features["spectral"],
            "speech_rate": features["speech_rate"],
            "duration": features["duration"],
        },
        "mapping_version": "v1",
    }


def generate_artwork(features: dict[str, float], theme: str) -> dict[str, Any]:
    """Generate the current deterministic artwork payload.

    This is the seam where the real SVG/procedural/image-generation engine can
    be inserted later without changing the API or recording workflow.
    """
    artwork_id = str(uuid.uuid4())
    visual_parameters = build_visual_parameters(features, theme)

    return {
        "artwork_id": artwork_id,
        "visual_parameters": visual_parameters,
        "artwork_url": None,
    }


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "reotoi · voice art"},
    )


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="app.html",
        context={
            "title": "Create artwork",
            "max_recording_seconds": MAX_RECORDING_SECONDS,
            "themes": sorted(ALLOWED_THEMES - {"surprise"}),
        },
    )


@app.get("/gallery", response_class=HTMLResponse)
async def gallery_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={"title": "Gallery"},
    )


# ---------------------------------------------------------------------------
# Artwork generation action
# ---------------------------------------------------------------------------


@app.post("/generate")
async def generate_voice_art(
    audio: Annotated[UploadFile, File(...)],
    theme: Annotated[str, Form()] = "surprise",
    input_source: Annotated[str, Form()] = "microphone",
) -> dict[str, Any]:
    """Convert a microphone recording or fallback audio upload into art data."""
    theme = validate_theme(theme)
    input_source = validate_input_source(input_source)
    temp_path, total_bytes = await save_upload_to_temp(audio)

    try:
        acoustic_features = analyze_audio(temp_path)
        speech_analysis = analyze_speech_with_assemblyai(temp_path)

        assembly_duration = speech_analysis.get("duration_seconds") or 0.0
        if assembly_duration > MAX_RECORDING_SECONDS + 0.25:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.",
            )

        artwork = generate_artwork(acoustic_features, theme)

        logger.info(
            "Generated artwork input=%s theme=%s bytes=%s artwork_id=%s",
            input_source,
            theme,
            total_bytes,
            artwork["artwork_id"],
        )

        return {
            "success": True,
            "message": "Your voice has been translated into visual parameters.",
            "artwork_id": artwork["artwork_id"],
            "theme": theme,
            "input_source": input_source,
            "voice_dna": calculate_voice_dna(acoustic_features),
            "transcript": speech_analysis.get("transcript"),
            "artwork_url": artwork.get("artwork_url"),
            "visual_parameters": artwork["visual_parameters"],
        }
    finally:
        delete_temp_file(temp_path)


@app.get("/404", response_class=HTMLResponse, include_in_schema=False)
async def not_found_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={"title": "Page not found"},
        status_code=status.HTTP_404_NOT_FOUND,
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={"title": "Page not found", "requested_path": request.url.path},
        status_code=status.HTTP_404_NOT_FOUND,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> HTMLResponse | JSONResponse:
    # Page requests receive a rendered HTML error instead of a JSON error blob.
    # The /generate action is called by browser JavaScript, so validation/service
    # errors remain JSON there for the frontend to display in context.
    if request.url.path == "/generate":
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return templates.TemplateResponse(
        request=request,
        name="404.html" if exc.status_code == 404 else "500.html",
        context={"title": "Page not found" if exc.status_code == 404 else "Something went wrong", "requested_path": request.url.path, "error_message": str(exc.detail)},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> HTMLResponse:
    logger.exception("Unhandled application error on %s", request.url.path, exc_info=exc)
    return templates.TemplateResponse(
        request=request,
        name="500.html",
        context={
            "title": "Something went wrong",
            "requested_path": request.url.path,
        },
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
