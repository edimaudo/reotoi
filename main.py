from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from services.art_generator import render_svg
from services.assemblyai_service import analyze_speech
from services.gallery_service import delete_artwork, list_gallery, save_artwork
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

# The browser currently normalizes microphone recordings and fallback uploads
# to WAV before submitting them. We still validate the actual file bytes below
# rather than trusting the multipart MIME label, because browsers can report
# valid WAV blobs as application/octet-stream or another generic type.
WAV_MIME_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "application/wav",
    "application/x-wav",
    "application/octet-stream",
    "",
}


def validate_theme(theme: str) -> str:
    """Validate and normalize the selected theme."""
    normalized = (theme or "surprise").strip().lower()
    if normalized not in ALLOWED_THEMES:
        raise HTTPException(status_code=422, detail="Invalid artwork theme.")
    return normalized


def validate_input_source(input_source: str) -> str:
    """Validate the explicit source selected by the browser."""
    normalized = (input_source or "microphone").strip().lower()
    if normalized not in ALLOWED_INPUT_SOURCES:
        raise HTTPException(status_code=422, detail="Invalid audio input source.")
    return normalized


def normalized_content_type(audio: UploadFile) -> str:
    """Return the MIME type without optional parameters such as codecs."""
    return (audio.content_type or "").lower().split(";", 1)[0].strip()


async def validate_audio_input(audio: UploadFile, input_source: str) -> bytes:
    """Validate the submitted audio by inspecting its bytes, not just its MIME label.

    The browser-side recorder produces WAV for the microphone and currently also
    normalizes fallback uploads to WAV. Multipart MIME metadata is not considered
    authoritative because browsers may report a generic content type.
    """
    content_type = normalized_content_type(audio)
    if content_type not in WAV_MIME_TYPES:
        logger.info(
            "Non-WAV MIME label received; validating actual bytes. source=%s content_type=%s filename=%s",
            input_source,
            content_type or "<missing>",
            audio.filename or "<unnamed>",
        )

    header = await audio.read(12)
    await audio.seek(0)

    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        if input_source == "microphone":
            detail = (
                "reotoi could not read this recording. The microphone data was not "
                "received as a valid WAV file. Please record again."
            )
        else:
            detail = (
                "reotoi could not read this audio file. Please choose a supported "
                "audio file and try again."
            )
        raise HTTPException(status_code=415, detail=detail)

    return header


def audio_suffix(_: UploadFile) -> str:
    """Use a WAV suffix because the validated processing format is WAV."""
    return ".wav"


async def save_audio_temporarily(
    audio: UploadFile,
    input_source: str,
) -> tuple[str, int]:
    """Stream validated WAV audio to a temporary file with size protection."""
    await validate_audio_input(audio, input_source)
    total = 0
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=audio_suffix(audio))
    temp_path = handle.name

    try:
        with handle:
            while chunk := await audio.read(1024 * 512):
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The normalized audio must be 4 MB or smaller for this web deployment.",
                    )
                handle.write(chunk)
        if total < MIN_AUDIO_BYTES:
            raise HTTPException(status_code=400, detail="The audio recording is empty or too short.")
        return temp_path, total
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def cleanup_temp_file(file_path: str) -> None:
    """Remove temporary audio after processing."""
    try:
        os.unlink(file_path)
    except OSError:
        pass


def get_gallery_id(request: Request) -> str:
    """Get the anonymous browser gallery identifier or create one."""
    return request.cookies.get("reotoi_gallery") or uuid.uuid4().hex


def attach_gallery_cookie(request: Request, response: Response, gallery_id: str | None = None) -> Response:
    """Ensure the anonymous gallery identifier survives browser navigation."""
    if request.cookies.get("reotoi_gallery"):
        return response
    response.set_cookie(
        key="reotoi_gallery",
        value=gallery_id or uuid.uuid4().hex,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=bool(os.getenv("VERCEL")),
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "reotoi · voice art"},
    )
    return attach_gallery_cookie(request, response)


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="app.html",
        context={
            "title": "Create artwork",
            "max_recording_seconds": MAX_RECORDING_SECONDS,
            "themes": ["abstract", "nature", "cosmos", "architecture", "organic", "geometric"],
        },
    )
    return attach_gallery_cookie(request, response)


@app.get("/gallery", response_class=HTMLResponse)
async def gallery_page(request: Request) -> HTMLResponse:
    gallery_id = get_gallery_id(request)
    items = await list_gallery(gallery_id)
    response = templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={"title": "Gallery", "items": items},
    )
    return attach_gallery_cookie(request, response, gallery_id)


@app.post("/generate")
async def generate_voice_art(
    audio: Annotated[UploadFile, File(...)],
    theme: Annotated[str, Form()] = "surprise",
    input_source: Annotated[str, Form()] = "microphone",
) -> JSONResponse:
    """Process submitted voice audio and return the generated artwork."""
    validated_theme = validate_theme(theme)
    validated_source = validate_input_source(input_source)
    temp_path, byte_count = await save_audio_temporarily(audio, validated_source)

    try:
        acoustic_features = analyze_audio(temp_path)
        speech_analysis = analyze_speech(temp_path)
        duration = speech_analysis.get("duration_seconds") or 0.0
        if duration > MAX_RECORDING_SECONDS + 0.25:
            raise HTTPException(status_code=422, detail=f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.")

        artwork_id, artwork_url, visual_parameters = render_svg(
            acoustic_features,
            validated_theme,
        )
        voice_dna = calculate_voice_dna(acoustic_features)
        resolved_theme = visual_parameters["theme"]

        logger.info(
            "Created artwork id=%s source=%s theme=%s bytes=%s",
            artwork_id,
            validated_source,
            resolved_theme,
            byte_count,
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
        raise HTTPException(status_code=503, detail="Voice analysis service is unavailable right now. Please try again later.") from exc
    except Exception as exc:
        logger.exception("Unexpected generation failure")
        raise HTTPException(status_code=500, detail="reotoi could not create the artwork. Please try again later.") from exc
    finally:
        cleanup_temp_file(temp_path)


@app.post("/gallery/save")
async def save_gallery_artwork(
    request: Request,
    artwork_id: Annotated[str, Form(...)],
    artwork_url: Annotated[str, Form(...)],
    theme: Annotated[str, Form(...)],
    voice_dna: Annotated[str, Form(...)],
) -> JSONResponse:
    """Save the most recently generated artwork to the current browser gallery."""
    try:
        dna = json.loads(voice_dna)
        if not isinstance(dna, dict):
            raise ValueError("Invalid Voice DNA.")
        item = await save_artwork(
            get_gallery_id(request),
            artwork_id,
            artwork_url,
            validate_theme(theme),
            dna,
        )
        return JSONResponse({"success": True, "item": item})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Gallery save failed")
        raise HTTPException(status_code=503, detail="Gallery storage is unavailable right now. Please try again later.") from exc


@app.post("/gallery/delete/{artwork_id}")
async def remove_gallery_artwork(request: Request, artwork_id: str) -> JSONResponse:
    """Delete artwork from the current browser gallery."""
    try:
        deleted = await delete_artwork(get_gallery_id(request), artwork_id)
        return JSONResponse({"success": deleted})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Gallery delete failed")
        raise HTTPException(status_code=503, detail="Gallery storage is unavailable right now. Please try again later.") from exc


@app.get("/404", response_class=HTMLResponse, include_in_schema=False)
async def explicit_not_found_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={"title": "Page not found", "requested_path": request.url.path},
        status_code=404,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """Render browser-facing errors as HTML, except /generate and action routes."""
    if request.url.path.startswith("/generate") or request.url.path.startswith("/gallery/"):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})

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
async def unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    logger.exception("Unhandled application error at %s", request.url.path, exc_info=exc)
    return templates.TemplateResponse(
        request=request,
        name="500.html",
        context={"title": "Something went wrong", "requested_path": request.url.path},
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
