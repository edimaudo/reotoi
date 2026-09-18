from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from services.art_generator import render_svg
from services.audio_conversion import normalize_audio
from services.assemblyai_service import analyze_speech
from services.voice_features import analyze_audio, calculate_voice_dna

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reotoi")

app = FastAPI(
    title="reotoi"
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_RECORDING_SECONDS = 30
MAX_AUDIO_BYTES = 4 * 1024 * 1024
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


def validate_theme(theme: str) -> str:
    """Validate and normalize the selected theme."""
    normalized = (theme or "surprise").strip().lower()
    if normalized not in ALLOWED_THEMES:
        raise HTTPException(status_code=422, detail="Invalid artwork theme.")
    return normalized


def validate_input_source(input_source: str) -> str:
    """Validate the browser-selected audio source label."""
    normalized = (input_source or "").strip().lower()
    if normalized not in ALLOWED_INPUT_SOURCES:
        raise HTTPException(status_code=422, detail="Invalid audio input source.")
    return normalized


def _safe_audio_suffix(audio: UploadFile) -> str:
    """Return the original filename suffix when it is safe to keep.

    Audio decoding does not depend on this suffix. The conversion service
    inspects the actual uploaded bytes, so an unknown or missing extension is
    still safe.
    """
    suffix = Path(audio.filename or "").suffix.lower()

    allowed_suffixes = {
        ".wav",
        ".wave",
        ".mp3",
        ".ogg",
        ".oga",
        ".flac",
        ".aif",
        ".aiff",
        ".au",
        ".snd",
        ".mp4",
        ".m4a",
        ".webm",
        ".aac",
    }

    return suffix if suffix in allowed_suffixes else ".audio"


async def save_audio_temporarily(audio: UploadFile) -> tuple[str, int]:
    """Save the original upload without depending on its MIME type."""
    total = 0
    suffix = _safe_audio_suffix(audio)
    handle = tempfile.NamedTemporaryFile(
        prefix="reotoi-input-",
        suffix=suffix,
        delete=False,
    )
    temp_path = handle.name

    try:
        with handle:
            while chunk := await audio.read(1024 * 512):
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The prepared audio is too large to process.",
                    )
                handle.write(chunk)

        if total < MIN_AUDIO_BYTES:
            raise HTTPException(
                status_code=400,
                detail="The audio recording is empty or too short.",
            )

        return temp_path, total
    except Exception:
        cleanup_temp_file(temp_path)
        raise


def cleanup_temp_file(file_path: str | None) -> None:
    """Remove a temporary file if it exists."""
    if not file_path:
        return

    try:
        os.unlink(file_path)
    except OSError:
        pass




@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "reotoi · voice art"},
    )


@app.get("/create", response_class=HTMLResponse)
async def app_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="app.html",
        context={
            "title": "Create artwork",
            "max_recording_seconds": MAX_RECORDING_SECONDS,
            "themes": [
                "abstract",
                "nature",
                "cosmos",
                "architecture",
                "organic",
                "geometric",
            ],
        },
    )


@app.get("/gallery", response_class=HTMLResponse)
async def gallery_page(request: Request) -> HTMLResponse:
    """Render the browser-local gallery shell."""
    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={"title": "Gallery"},
    )


@app.post("/generate")
async def generate_voice_art(
    audio: Annotated[UploadFile, File(...)],
    input_source: Annotated[str, Form(...)],
    theme: Annotated[str, Form()] = "surprise",
) -> JSONResponse:
    """Decode, normalize and analyze one microphone or audio-file input."""
    validated_theme = validate_theme(theme)
    validated_source = validate_input_source(input_source)

    temp_path, byte_count = await save_audio_temporarily(audio)
    normalized_path = None

    try:
        # audio_conversion.py detects supported formats from the actual uploaded
        # bytes. Browser-normalized WAV is also accepted through the same path.
        normalized_path = normalize_audio(
            temp_path,
            max_duration_seconds=MAX_RECORDING_SECONDS,
        )

        acoustic_features = analyze_audio(normalized_path)
        speech_analysis = analyze_speech(normalized_path)

        duration = speech_analysis.get("duration_seconds") or 0.0
        if duration > MAX_RECORDING_SECONDS + 0.25:
            raise HTTPException(
                status_code=422,
                detail=f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.",
            )

        artwork_id, artwork_url, visual_parameters = render_svg(
            acoustic_features,
            validated_theme,
        )
        voice_dna = calculate_voice_dna(acoustic_features)
        resolved_theme = visual_parameters["theme"]

        logger.info(
            "Created artwork id=%s source=%s theme=%s bytes=%s original_filename=%r",
            artwork_id,
            validated_source,
            resolved_theme,
            byte_count,
            audio.filename,
        )

        return JSONResponse(
            content={
                "success": True,
                "message": "Your voice has been translated into visual parameters.",
                "artwork_id": artwork_id,
                "theme": resolved_theme,
                "input_source": validated_source,
                "voice_dna": voice_dna,
                "transcript": speech_analysis.get("transcript"),
                "artwork_url": artwork_url,
                "visual_parameters": visual_parameters,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except RuntimeError as exc:
        logger.exception("Service failure while generating artwork")
        raise HTTPException(
            status_code=503,
            detail="Voice analysis service is unavailable right now. Please try again later.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected generation failure")
        raise HTTPException(
            status_code=500,
            detail="reotoi could not create the artwork. Please try again later.",
        ) from exc
    finally:
        cleanup_temp_file(temp_path)
        cleanup_temp_file(normalized_path)



@app.get("/404", response_class=HTMLResponse, include_in_schema=False)
async def explicit_not_found_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={"title": "Page not found", "requested_path": request.url.path},
        status_code=404,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    """Render browser-facing errors as HTML except action routes."""
    if request.url.path.startswith("/generate") or request.url.path.startswith("/gallery/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc.detail)},
        )

    if exc.status_code == 404:
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={"title": "Page not found", "requested_path": request.url.path},
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="500.html",
        context={
            "title": "Something went wrong",
            "requested_path": request.url.path,
            "error_message": str(exc.detail),
        },
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> HTMLResponse:
    logger.exception(
        "Unhandled application error at %s",
        request.url.path,
        exc_info=exc,
    )
    return templates.TemplateResponse(
        request=request,
        name="500.html",
        context={
            "title": "Something went wrong",
            "requested_path": request.url.path,
        },
        status_code=500,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
