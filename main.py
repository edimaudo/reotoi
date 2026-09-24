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
from services.audio_conversion import normalize_audio
from services.gallery_service import delete_artwork, list_gallery, save_artwork
from services.voice_features import analyze_audio, calculate_voice_dna

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reotoi")

app = FastAPI(title="reotoi")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_RECORDING_SECONDS = 300
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

#### Routes ####
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
        normalized_path = normalize_audio(
            temp_path,
            max_duration_seconds=MAX_RECORDING_SECONDS,
        )

        acoustic_features = analyze_audio(normalized_path)
        #speech_analysis = analyze_speech(normalized_path)

        # duration = speech_analysis.get("duration_seconds") or 0.0
        # if duration > MAX_RECORDING_SECONDS + 0.25:
        #     raise HTTPException(
        #         status_code=422,
        #         detail=f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.",
        #     )

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

#  BASE_DIR = Path(__file__).resolve().parent
# TEMPLATES_DIR = BASE_DIR / "templates"
# STATIC_DIR = BASE_DIR / "static"

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("reotoi")

# app = FastAPI(
#     title="reotoi · voice art"
# )

# app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# MAX_RECORDING_SECONDS = 30
# MAX_AUDIO_BYTES = 4 * 1024 * 1024
# MIN_AUDIO_BYTES = 512
# ALLOWED_INPUT_SOURCES = {"microphone", "upload"}

# ALLOWED_THEMES = {
#     "abstract",
#     "nature",
#     "cosmos",
#     "architecture",
#     "organic",
#     "geometric",
#     "surprise",
# }


# def validate_theme(theme: str) -> str:
#     """Validate and normalize the selected theme."""
#     normalized = (theme or "surprise").strip().lower()

#     if normalized not in ALLOWED_THEMES:
#         raise HTTPException(
#             status_code=422,
#             detail="Invalid artwork theme.",
#         )

#     return normalized


# def validate_input_source(input_source: str) -> str:
#     """Validate the browser-selected audio source label."""
#     normalized = (input_source or "").strip().lower()

#     if normalized not in ALLOWED_INPUT_SOURCES:
#         raise HTTPException(
#             status_code=422,
#             detail="Invalid audio input source.",
#         )

#     return normalized


# def _safe_audio_suffix(audio: UploadFile) -> str:
#     """Return a safe extension that preserves the uploaded audio format."""
#     filename_suffix = Path(audio.filename or "").suffix.lower()

#     mime_to_suffix = {
#         "audio/mpeg": ".mp3",
#         "audio/mp3": ".mp3",
#         "audio/ogg": ".ogg",
#         "audio/wav": ".wav",
#         "audio/wave": ".wav",
#         "audio/x-wav": ".wav",
#         "audio/flac": ".flac",
#         "audio/mp4": ".mp4",
#         "video/mp4": ".mp4",
#         "audio/x-m4a": ".m4a",
#         "audio/webm": ".webm",
#         "video/webm": ".webm",
#         "audio/aac": ".aac",
#     }

#     if filename_suffix in {
#         ".wav",
#         ".wave",
#         ".mp3",
#         ".ogg",
#         ".oga",
#         ".flac",
#         ".mp4",
#         ".m4a",
#         ".webm",
#         ".aac",
#         ".aif",
#         ".aiff",
#         ".au",
#         ".snd",
#     }:
#         return filename_suffix

#     content_type = (
#         (audio.content_type or "")
#         .lower()
#         .split(";", 1)[0]
#         .strip()
#     )

#     return mime_to_suffix.get(content_type, ".bin")


# async def save_audio_temporarily(audio: UploadFile) -> tuple[str, int]:
#     """Save the original upload while preserving its audio format suffix."""
#     total = 0
#     suffix = _safe_audio_suffix(audio)

#     handle = tempfile.NamedTemporaryFile(
#         prefix="reotoi-input-",
#         suffix=suffix,
#         delete=False,
#     )

#     temp_path = handle.name

#     try:
#         with handle:
#             while chunk := await audio.read(1024 * 512):
#                 total += len(chunk)

#                 if total > MAX_AUDIO_BYTES:
#                     raise HTTPException(
#                         status_code=413,
#                         detail="The prepared audio is too large to process.",
#                     )

#                 handle.write(chunk)

#         if total < MIN_AUDIO_BYTES:
#             raise HTTPException(
#                 status_code=400,
#                 detail="The audio recording is empty or too short.",
#             )

#         return temp_path, total

#     except Exception:
#         cleanup_temp_file(temp_path)
#         raise


# def cleanup_temp_file(file_path: str | None) -> None:
#     """Remove a temporary file if it exists."""
#     if not file_path:
#         return

#     try:
#         os.unlink(file_path)
#     except OSError:
#         pass


# def get_gallery_id(request: Request) -> str:
#     """Get the anonymous browser gallery identifier or create one."""
#     return request.cookies.get("reotoi_gallery") or uuid.uuid4().hex


# def attach_gallery_cookie(
#     request: Request,
#     response: Response,
#     gallery_id: str | None = None,
# ) -> Response:
#     """Ensure the anonymous gallery identifier survives browser navigation."""
#     if request.cookies.get("reotoi_gallery"):
#         return response

#     response.set_cookie(
#         key="reotoi_gallery",
#         value=gallery_id or uuid.uuid4().hex,
#         max_age=60 * 60 * 24 * 365,
#         httponly=True,
#         samesite="lax",
#         secure=bool(os.getenv("VERCEL")),
#     )

#     return response


# @app.get("/", response_class=HTMLResponse)
# async def landing_page(request: Request) -> HTMLResponse:
#     response = templates.TemplateResponse(
#         request=request,
#         name="index.html",
#         context={"title": "reotoi · voice art"},
#     )

#     return attach_gallery_cookie(request, response)


# @app.get("/create", response_class=HTMLResponse)
# async def app_page(request: Request) -> HTMLResponse:
#     response = templates.TemplateResponse(
#         request=request,
#         name="app.html",
#         context={
#             "title": "Create artwork",
#             "max_recording_seconds": MAX_RECORDING_SECONDS,
#             "themes": [
#                 "abstract",
#                 "nature",
#                 "cosmos",
#                 "architecture",
#                 "organic",
#                 "geometric",
#             ],
#         },
#     )

#     return attach_gallery_cookie(request, response)




# @app.get("/gallery", response_class=HTMLResponse)
# async def gallery_page(request: Request) -> HTMLResponse:
#     """Render the browser-local gallery shell."""
#     return templates.TemplateResponse(
#         request=request,
#         name="gallery.html",
#         context={"title": "Gallery"},
#     )


# # @app.post("/generate")
# # async def generate_voice_art(
# #     audio: Annotated[UploadFile, File(...)],
# #     input_source: Annotated[str, Form(...)],
# #     theme: Annotated[str, Form()] = "surprise",
# # ) -> JSONResponse:
# #     """Decode, normalize and analyze one microphone or audio-file input."""
# #     validated_theme = validate_theme(theme)
# #     validated_source = validate_input_source(input_source)

# #     temp_path, byte_count = await save_audio_temporarily(audio)
# #     normalized_path = None

# #     try:
# #         # Directly decodable formats such as MP3, OGG, FLAC and WAV are handled
# #         # by the audio service. Browser-only container/codec inputs such as MP4,
# #         # M4A and WebM arrive here only after browser-side normalization.
# #         normalized_path = normalize_audio(
# #             temp_path,
# #             max_duration_seconds=MAX_RECORDING_SECONDS,
# #         )

# #         # This is the analysis that actually drives the artwork.
# #         acoustic_features = analyze_audio(normalized_path)

# #         # Duration is already calculated by analyze_audio(), so AssemblyAI is
# #         # not needed for duration validation.
# #         duration = acoustic_features.get("duration") or 0.0

# #         if duration > MAX_RECORDING_SECONDS + 0.25:
# #             raise HTTPException(
# #                 status_code=422,
# #                 detail=f"Audio must be {MAX_RECORDING_SECONDS} seconds or less.",
# #             )

# #         # The artwork generator receives the acoustic features directly.
# #         artwork_id, artwork_url, visual_parameters = render_svg(
# #             acoustic_features,
# #             validated_theme,
# #         )

# #         voice_dna = calculate_voice_dna(acoustic_features)
# #         resolved_theme = visual_parameters["theme"]

# #         logger.info(
# #             "Created artwork id=%s source=%s theme=%s bytes=%s original_filename=%r",
# #             artwork_id,
# #             validated_source,
# #             resolved_theme,
# #             byte_count,
# #             audio.filename,
# #         )

# #         return JSONResponse(
# #             content={
# #                 "success": True,
# #                 "message": "Your voice has been translated into visual parameters.",
# #                 "artwork_id": artwork_id,
# #                 "theme": resolved_theme,
# #                 "input_source": validated_source,
# #                 "voice_dna": voice_dna,
# #                 "artwork_url": artwork_url,
# #                 "visual_parameters": visual_parameters,
# #             }
# #         )

# #     except ValueError as exc:
# #         raise HTTPException(
# #             status_code=422,
# #             detail=str(exc),
# #         ) from exc

# #     except HTTPException:
# #         raise

# #     except RuntimeError as exc:
# #         logger.exception(
# #             "Service failure while generating artwork"
# #         )

# #         raise HTTPException(
# #             status_code=503,
# #             detail=(
# #                 "Voice analysis service is unavailable right now. "
# #                 "Please try again later."
# #             ),
# #         ) from exc

# #     except Exception as exc:
# #         logger.exception(
# #             "Unexpected generation failure"
# #         )

# #         raise HTTPException(
# #             status_code=500,
# #             detail=(
# #                 "reotoi could not create the artwork. "
# #                 "Please try again later."
# #             ),
# #         ) from exc

# #     finally:
# #         cleanup_temp_file(temp_path)
# #         cleanup_temp_file(normalized_path)


# # @app.post("/gallery/save")
# # async def save_gallery_artwork(
# #     request: Request,
# #     artwork_id: Annotated[str, Form(...)],
# #     artwork_url: Annotated[str, Form(...)],
# #     theme: Annotated[str, Form(...)],
# #     voice_dna: Annotated[str, Form(...)],
# # ) -> JSONResponse:
# #     """Save the most recently generated artwork to the current browser gallery."""
# #     try:
# #         dna = json.loads(voice_dna)

# #         if not isinstance(dna, dict):
# #             raise ValueError("Invalid Voice DNA.")

# #         item = await save_artwork(
# #             get_gallery_id(request),
# #             artwork_id,
# #             artwork_url,
# #             validate_theme(theme),
# #             dna,
# #         )

# #         return JSONResponse(
# #             {
# #                 "success": True,
# #                 "item": item,
# #             }
# #         )

# #     except ValueError as exc:
# #         raise HTTPException(
# #             status_code=422,
# #             detail=str(exc),
# #         ) from exc

# #     except Exception as exc:
# #         logger.exception("Gallery save failed")

# #         raise HTTPException(
# #             status_code=503,
# #             detail=(
# #                 "Gallery storage is unavailable right now. "
# #                 "Please try again later."
# #             ),
# #         ) from exc


# # @app.post("/gallery/delete/{artwork_id}")
# # async def remove_gallery_artwork(
# #     request: Request,
# #     artwork_id: str,
# # ) -> JSONResponse:
# #     """Delete artwork from the current browser gallery."""
# #     try:
# #         deleted = await delete_artwork(
# #             get_gallery_id(request),
# #             artwork_id,
# #         )

# #         return JSONResponse(
# #             {
# #                 "success": deleted,
# #             }
# #         )

# #     except ValueError as exc:
# #         raise HTTPException(
# #             status_code=422,
# #             detail=str(exc),
# #         ) from exc

# #     except Exception as exc:
# #         logger.exception("Gallery delete failed")

# #         raise HTTPException(
# #             status_code=503,
# #             detail=(
# #                 "Gallery storage is unavailable right now. "
# #                 "Please try again later."
# #             ),
# #         ) from exc


# # @app.get(
# #     "/404",
# #     response_class=HTMLResponse,
# #     include_in_schema=False,
# # )
# # async def explicit_not_found_page(
# #     request: Request,
# # ) -> HTMLResponse:
# #     return templates.TemplateResponse(
# #         request=request,
# #         name="404.html",
# #         context={
# #             "title": "Page not found",
# #             "requested_path": request.url.path,
# #         },
# #         status_code=404,
# #     )


# # @app.exception_handler(StarletteHTTPException)
# # async def http_exception_handler(
# #     request: Request,
# #     exc: StarletteHTTPException,
# # ) -> Response:
# #     """Render browser-facing errors as HTML except action routes."""
# #     if (
# #         request.url.path.startswith("/generate")
# #         or request.url.path.startswith("/gallery/")
# #     ):
# #         return JSONResponse(
# #             status_code=exc.status_code,
# #             content={
# #                 "detail": str(exc.detail),
# #             },
# #         )

# #     if exc.status_code == 404:
# #         return templates.TemplateResponse(
# #             request=request,
# #             name="404.html",
# #             context={
# #                 "title": "Page not found",
# #                 "requested_path": request.url.path,
# #             },
# #             status_code=404,
# #         )

# #     return templates.TemplateResponse(
# #         request=request,
# #         name="500.html",
# #         context={
# #             "title": "Something went wrong",
# #             "requested_path": request.url.path,
# #             "error_message": str(exc.detail),
# #         },
# #         status_code=exc.status_code,
# #     )


# # @app.exception_handler(Exception)
# # async def unhandled_exception_handler(
# #     request: Request,
# #     exc: Exception,
# # ) -> HTMLResponse:
# #     logger.exception(
# #         "Unhandled application error at %s",
# #         request.url.path,
# #         exc_info=exc,
# #     )

# #     return templates.TemplateResponse(
# #         request=request,
# #         name="500.html",
# #         context={
# #             "title": "Something went wrong",
# #             "requested_path": request.url.path,
# #         },
# #         status_code=500,
# #     )


# # if __name__ == "__main__":
# #     import uvicorn

# #     uvicorn.run(
# #         "main:app",
# #         host=os.getenv("HOST", "127.0.0.1"),
# #         port=int(os.getenv("PORT", "8000")),
# #         reload=True,
# #     )
