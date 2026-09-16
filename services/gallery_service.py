"""Gallery persistence.

Local development uses the filesystem. Vercel deployments use Vercel Blob so
saved artwork is not tied to an ephemeral function filesystem.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx

from vercel.blob import AsyncBlobClient

BASE_DIR = Path(__file__).resolve().parents[1]
LOCAL_GALLERY_DIR = BASE_DIR / "data" / "gallery"


def use_vercel_blob() -> bool:
    """Use Vercel Blob when explicitly configured or running on Vercel."""
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL"))


def _safe_gallery_id(gallery_id: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    cleaned = "".join(ch for ch in gallery_id if ch in allowed)
    if len(cleaned) < 8:
        raise ValueError("Invalid gallery identifier.")
    return cleaned[:80]


def _local_dir(gallery_id: str) -> Path:
    directory = LOCAL_GALLERY_DIR / _safe_gallery_id(gallery_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _decode_svg_from_data_uri(data_uri: str) -> bytes:
    prefix = "data:image/svg+xml;charset=utf-8,"
    if not data_uri.startswith(prefix):
        raise ValueError("Only generated SVG artwork can be saved.")
    return unquote(data_uri[len(prefix) :]).encode("utf-8")


def _metadata(artwork_id: str, gallery_id: str, theme: str, voice_dna: dict) -> dict:
    return {
        "artwork_id": artwork_id,
        "gallery_id": gallery_id,
        "theme": theme,
        "voice_dna": voice_dna,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


async def save_artwork(
    gallery_id: str,
    artwork_id: str,
    artwork_url: str,
    theme: str,
    voice_dna: dict,
) -> dict:
    """Persist artwork and return gallery metadata."""
    gallery_id = _safe_gallery_id(gallery_id)
    item = _metadata(artwork_id, gallery_id, theme, voice_dna)

    if use_vercel_blob():
        svg_bytes = _decode_svg_from_data_uri(artwork_url)
        pathname = f"gallery/{gallery_id}/{artwork_id}.svg"
        async with AsyncBlobClient() as client:
            blob = await client.put(
                pathname,
                svg_bytes,
                access="public",
                content_type="image/svg+xml",
                add_random_suffix=False,
            )
            metadata_blob = await client.put(
                f"gallery/{gallery_id}/{artwork_id}.json",
                json.dumps(item).encode("utf-8"),
                access="public",
                content_type="application/json",
                add_random_suffix=False,
            )
            item.update({"artwork_url": blob.url, "metadata_url": metadata_blob.url})
        return item

    directory = _local_dir(gallery_id)
    svg_path = directory / f"{artwork_id}.svg"
    json_path = directory / f"{artwork_id}.json"
    svg_path.write_bytes(_decode_svg_from_data_uri(artwork_url))
    json_path.write_text(json.dumps({**item, "artwork_url": artwork_url}), encoding="utf-8")
    return {**item, "artwork_url": artwork_url}


async def list_gallery(gallery_id: str) -> list[dict]:
    """Return saved artwork for one anonymous browser gallery."""
    gallery_id = _safe_gallery_id(gallery_id)

    if use_vercel_blob():
        async with AsyncBlobClient() as client:
            page = await client.list_objects(prefix=f"gallery/{gallery_id}/", limit=100)
            metadata_items = [item for item in page.blobs if item.pathname.endswith(".json")]
            results = []
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                for item in metadata_items:
                    metadata_response = await http_client.get(item.url)
                    if metadata_response.status_code != 200:
                        continue
                    try:
                        metadata = metadata_response.json()
                    except ValueError:
                        continue
                    svg_pathname = item.pathname[:-5] + ".svg"
                    svg_candidates = [b for b in page.blobs if b.pathname == svg_pathname]
                    if not svg_candidates:
                        continue
                    metadata["artwork_url"] = svg_candidates[0].url
                    results.append(metadata)
            return sorted(results, key=lambda entry: entry.get("saved_at", ""), reverse=True)

    directory = LOCAL_GALLERY_DIR / gallery_id
    if not directory.exists():
        return []

    results = []
    for json_path in directory.glob("*.json"):
        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (directory / f"{json_path.stem}.svg").exists():
            results.append(item)
    return sorted(results, key=lambda entry: entry.get("saved_at", ""), reverse=True)


async def delete_artwork(gallery_id: str, artwork_id: str) -> bool:
    """Delete one artwork from the current gallery."""
    gallery_id = _safe_gallery_id(gallery_id)
    if not artwork_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in artwork_id):
        raise ValueError("Invalid artwork identifier.")

    if use_vercel_blob():
        svg_path = f"gallery/{gallery_id}/{artwork_id}.svg"
        json_path = f"gallery/{gallery_id}/{artwork_id}.json"
        async with AsyncBlobClient() as client:
            await client.delete([svg_path, json_path])
        return True

    directory = LOCAL_GALLERY_DIR / gallery_id
    deleted = False
    for suffix in (".svg", ".json"):
        path = directory / f"{artwork_id}{suffix}"
        if path.exists():
            path.unlink()
            deleted = True
    return deleted
