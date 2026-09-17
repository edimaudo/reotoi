from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from services.art_generator import render_svg
from services.assemblyai_service import analyze_speech
from services.gallery_service import (
    delete_artwork,
    list_gallery,
    save_artwork,
)
from services.voice_features import (
    analyze_audio,
    calculate_voice_dna,
)

# This service should convert supported uploaded audio formats such as
# MP3, M4A, OGG, FLAC, etc. into a normalized PCM WAV file.
#from services.audio_conversion import normalize_audio

BASE_DIR = Path(__file__).resolve().parent

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reotoi")


app = FastAPI(
    title="reotoi · voice art",
    description=(
        "Turn characteristics of a voice into unique visual artwork."
    ),
    version="1.0.0",
)

# Static files are required by base.html.
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


# ---------------------------------------------------------------------------
# Application configuration
# ---------------------------------------------------------------------------

MAX_RECORDING_SECONDS = 30

# Vercel's request-body limit means the application should keep the incoming
# audio payload below the deployment limit.
MAX_AUDIO_BYTES = 4 * 1024 * 1024

MIN_AUDIO_BYTES = 512

ALLOWED_INPUT_SOURCES = {
    "microphone",
    "upload",
}

ALLOWED_THEMES = {
    "abstract",
    "nature",
    "cosmos",
    "architecture",
    "organic",
    "geometric",
    "surprise",
}

ALLOWED_UPLOAD_EXTENSIONS = {
    ".wav",
    ".wave",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".oga",
    ".flac",
    ".aac",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_theme(theme: str | None) -> str:
    """Validate and normalize the selected artwork theme."""

    normalized = (theme or "surprise").strip().lower()

    if normalized not in ALLOWED_THEMES:
        raise HTTPException(
            status_code=422,
            detail="Invalid artwork theme.",
        )

    return normalized


def validate_input_source(
    input_source: str | None,
) -> str:
    """Require an explicit microphone/upload source.

    Never assume that a missing value means microphone.
    """

    if not input_source:
        raise HTTPException(
            status_code=400,
            detail=(
                "reotoi could not determine how the audio was provided. "
                "Please record with the microphone or choose an audio file again."
            ),
        )

    normalized = input_source.strip().lower()

    if normalized not in ALLOWED_INPUT_SOURCES:
        raise HTTPException(
            status_code=422,
            detail="Invalid audio input source.",
        )

    return normalized


def get_file_extension(audio: UploadFile) -> str:
    """Return the uploaded file extension in lowercase."""

    filename = Path(audio.filename or "")
    extension = filename.suffix.lower()

    return extension


def validate_upload_extension(
    audio: UploadFile,
    input_source: str,
) -> str:
    """Validate uploaded filename extension.

    Microphone input is expected to be generated as WAV by app.js.
    Uploaded files may use one of the supported common audio extensions.
    """

    extension = get_file_extension(audio)

    if input_source == "microphone":
        if extension not in {".wav", ".wave"}:
            raise HTTPException(
                status_code=415,
                detail=(
                    "reotoi could not read the microphone recording. "
                    "The microphone recording must be submitted as WAV. "
                    "Please record again."
                ),
            )

        return ".wav"

    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                "reotoi could not process this audio file. "
                "Please choose a supported audio file such as MP3, M4A, "
                "WAV, OGG, or FLAC."
            ),
        )

    return extension


async def validate_microphone_wav(
    audio: UploadFile,
) -> None:
    """Confirm that the microphone submission contains a real WAV header."""

    header = await audio.read(12)
    await audio.seek(0)

    is_wav = (
        len(header) >= 12
        and header[:4] == b"RIFF"
        and header[8:12] == b"WAVE"
    )

    if not is_wav:
        raise HTTPException(
            status_code=415,
            detail=(
                "reotoi could not read the microphone recording. "
                "The microphone data was not received as a valid WAV file. "
                "Please record again."
            ),
        )


# ---------------------------------------------------------------------------
# Temporary audio storage
# ---------------------------------------------------------------------------

async def save_upload_temporarily(
    audio: UploadFile,
    input_source: str,
) -> tuple[str, int]:
    """Save the incoming audio to a protected temporary file.

    For microphone recordings, the file must already be valid WAV.

    For uploaded files, preserve the original extension. The upload is then
    normalized by services.audio_conversion before acoustic analysis.
    """

    extension = validate_upload_extension(
        audio,
        input_source,
    )

    if input_source == "microphone":
        await validate_microphone_wav(audio)

    total = 0

    handle = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=extension,
    )

    temp_path = handle.name

    try:
        with handle:
            while True:
                chunk = await audio.read(1024 * 512)

                if not chunk:
                    break

                total += len(chunk)

                if total > MAX_AUDIO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "The audio file is too large for this deployment. "
                            "Please use a smaller recording."
                        ),
                    )

                handle.write(chunk)

        if total < MIN_AUDIO_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The audio recording is empty or too short. "
                    "Please provide a longer recording."
                ),
            )

        return temp_path, total

    except Exception:
        cleanup_temp_file(temp_path)
        raise


def cleanup_temp_file(
    file_path: str | None,
) -> None:
    """Remove a temporary file safely."""

    if not file_path:
        return

    try:
        os.unlink(file_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Gallery identity
# ---------------------------------------------------------------------------

def get_gallery_id(
    request: Request,
) -> str:
    """Return the anonymous browser gallery identifier."""

    return (
        request.cookies.get("reotoi_gallery")
        or uuid.uuid4().hex
    )


def attach_gallery_cookie(
    request: Request,
    response: Response,
    gallery_id: str | None = None,
) -> Response:
    """Create an anonymous gallery cookie when one does not exist."""

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


# ---------------------------------------------------------------------------
# Web pages
# ---------------------------------------------------------------------------

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def landing_page(
    request: Request,
) -> HTMLResponse:

    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "reotoi · voice art",
        },
    )

    return attach_gallery_cookie(
        request,
        response,
    )


@app.get(
    "/app",
    response_class=HTMLResponse,
)
async def app_page(
    request: Request,
) -> HTMLResponse:

    response = templates.TemplateResponse(
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

    return attach_gallery_cookie(
        request,
        response,
    )


@app.get(
    "/gallery",
    response_class=HTMLResponse,
)
async def gallery_page(
    request: Request,
) -> HTMLResponse:

    gallery_id = get_gallery_id(request)

    items = await list_gallery(
        gallery_id
    )

    response = templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={
            "title": "Gallery",
            "items": items,
        },
    )

    return attach_gallery_cookie(
        request,
        response,
        gallery_id,
    )


# ---------------------------------------------------------------------------
# Artwork generation
# ---------------------------------------------------------------------------

@app.post("/generate")
async def generate_voice_art(
    request: Request,
    audio: Annotated[
        UploadFile,
        File(...),
    ],
    theme: Annotated[
        str | None,
        Form(),
    ] = None,
    input_source: Annotated[
        str | None,
        Form(),
    ] = None,
) -> JSONResponse:
    """Generate artwork from microphone or uploaded audio."""

    validated_theme = validate_theme(theme)

    validated_source = validate_input_source(
        input_source
    )

    logger.info(
        "Audio request received: source=%s filename=%s content_type=%s",
        validated_source,
        audio.filename or "<unnamed>",
        audio.content_type or "<missing>",
    )

    temp_path: str | None = None

    try:
        # ---------------------------------------------------------------
        # 1. Save the original incoming file.
        # ---------------------------------------------------------------

        temp_path, byte_count = await save_upload_temporarily(
            audio,
            validated_source,
        )

        original_path = temp_path

        # ---------------------------------------------------------------
        # 2. Normalize audio.
        #
        # Microphone input is already WAV.
        # Uploads are normalized from their original format into WAV.
        # ---------------------------------------------------------------

        if validated_source == "upload":
            analysis_path = normalize_audio(
                original_path
            )
        else:
            analysis_path = original_path

        # ---------------------------------------------------------------
        # 3. Analyze acoustic characteristics.
        # ---------------------------------------------------------------

        acoustic_features = analyze_audio(
            analysis_path
        )

        # ---------------------------------------------------------------
        # 4. Analyze speech with AssemblyAI.
        # ---------------------------------------------------------------

        speech_analysis = analyze_speech(
            analysis_path
        )

        duration = (
            speech_analysis.get(
                "duration_seconds"
            )
            or acoustic_features.get(
                "duration"
            )
            or 0.0
        )

        if duration > MAX_RECORDING_SECONDS + 0.25:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Audio must be "
                    f"{MAX_RECORDING_SECONDS} seconds or less."
                ),
            )

        if duration <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "reotoi could not determine the audio duration. "
                    "Please provide another recording."
                ),
            )

        # ---------------------------------------------------------------
        # 5. Convert voice characteristics into artwork.
        # ---------------------------------------------------------------

        artwork_id, artwork_url, visual_parameters = render_svg(
            acoustic_features,
            validated_theme,
        )

        voice_dna = calculate_voice_dna(
            acoustic_features
        )

        resolved_theme = visual_parameters["theme"]

        logger.info(
            (
                "Created artwork id=%s "
                "source=%s "
                "theme=%s "
                "bytes=%s"
            ),
            artwork_id,
            validated_source,
            resolved_theme,
            byte_count,
        )

        # ---------------------------------------------------------------
        # 6. Return result to the web application.
        # ---------------------------------------------------------------

        return JSONResponse(
            content={
                "success": True,
                "message": (
                    "Your voice has been translated "
                    "into visual parameters."
                ),
                "artwork_id": artwork_id,
                "theme": resolved_theme,
                "input_source": validated_source,
                "voice_dna": voice_dna,
                "transcript": speech_analysis.get(
                    "transcript"
                ),
                "artwork_url": artwork_url,
                "visual_parameters": visual_parameters,
            }
        )

    except HTTPException:
        raise

    except ValueError as exc:
        logger.exception(
            "Validation error while generating artwork"
        )

        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        logger.exception(
            "Service failure while generating artwork"
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "reotoi's voice analysis service "
                "is unavailable right now. "
                "Please try again later."
            ),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Unexpected generation failure"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "reotoi could not create the artwork. "
                "Please try again later."
            ),
        ) from exc

    finally:
        cleanup_temp_file(temp_path)


# ---------------------------------------------------------------------------
# Gallery actions
# ---------------------------------------------------------------------------

@app.post("/gallery/save")
async def save_gallery_artwork(
    request: Request,
    artwork_id: Annotated[
        str,
        Form(...),
    ],
    artwork_url: Annotated[
        str,
        Form(...),
    ],
    theme: Annotated[
        str,
        Form(...),
    ],
    voice_dna: Annotated[
        str,
        Form(...),
    ],
) -> JSONResponse:

    try:
        dna = json.loads(
            voice_dna
        )

        if not isinstance(dna, dict):
            raise ValueError(
                "Invalid Voice DNA."
            )

        item = await save_artwork(
            get_gallery_id(request),
            artwork_id,
            artwork_url,
            validate_theme(theme),
            dna,
        )

        return JSONResponse(
            {
                "success": True,
                "item": item,
            }
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Gallery save failed"
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "Gallery storage is unavailable "
                "right now. Please try again later."
            ),
        ) from exc


@app.post(
    "/gallery/delete/{artwork_id}"
)
async def remove_gallery_artwork(
    request: Request,
    artwork_id: str,
) -> JSONResponse:

    try:
        deleted = await delete_artwork(
            get_gallery_id(request),
            artwork_id,
        )

        return JSONResponse(
            {
                "success": deleted,
            }
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Gallery delete failed"
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "Gallery storage is unavailable "
                "right now. Please try again later."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# 404 and browser-facing errors
# ---------------------------------------------------------------------------

@app.get(
    "/404",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def explicit_not_found_page(
    request: Request,
) -> HTMLResponse:

    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={
            "title": "Page not found",
            "requested_path": request.url.path,
        },
        status_code=404,
    )


@app.exception_handler(
    StarletteHTTPException
)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    """Return JSON for form/action failures and HTML for page failures."""

    action_routes = (
        request.url.path == "/generate"
        or request.url.path.startswith("/gallery/")
    )

    if action_routes:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": str(exc.detail),
            },
        )

    if exc.status_code == 404:
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={
                "title": "Page not found",
                "requested_path": request.url.path,
            },
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
) -> Response:
    """Render browser-friendly errors instead of raw JSON."""

    logger.exception(
        "Unhandled application error at %s",
        request.url.path,
        exc_info=exc,
    )

    action_routes = (
        request.url.path == "/generate"
        or request.url.path.startswith("/gallery/")
    )

    if action_routes:
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "reotoi could not create the artwork. "
                    "Please try again later."
                ),
            },
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


# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv(
            "HOST",
            "127.0.0.1",
        ),
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
        reload=True,
    )
