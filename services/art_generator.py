"""Deterministic artwork generation for reotoi.

The MVP uses procedural SVG so the relationship between the voice feature vector
and the visible result is explicit and reproducible. A different rendering engine
can replace this module later without changing the recording workflow.
"""

from __future__ import annotations

import html
import json
import math
import random
import uuid
from urllib.parse import quote

THEMES = {
    "abstract": {"background": "#0f1720", "strokes": ["#f3efe5", "#8fb8a8", "#d6a85d"]},
    "nature": {"background": "#102018", "strokes": ["#c8d7bd", "#82a878", "#d2b48c"]},
    "cosmos": {"background": "#11101f", "strokes": ["#d9d2ff", "#8e9be8", "#e5bd78"]},
    "architecture": {"background": "#161616", "strokes": ["#e8e2d6", "#9ca6b4", "#c79559"]},
    "organic": {"background": "#191915", "strokes": ["#d9d0b8", "#a8b58e", "#cf9d77"]},
    "geometric": {"background": "#10171b", "strokes": ["#e6e7e3", "#7aa3ad", "#d29a62"]},
}


def choose_theme(theme: str) -> str:
    """Resolve Surprise Me into one deterministic theme for this artwork."""
    if theme != "surprise":
        return theme
    return random.SystemRandom().choice(sorted(THEMES))


def build_visual_parameters(features: dict[str, float], theme: str) -> dict:
    """Create the explicit intermediate representation used by the renderer."""
    resolved_theme = choose_theme(theme)
    return {
        "theme": resolved_theme,
        "voice": {
            "pitch": round(features.get("pitch", 0.0), 4),
            "energy": round(features.get("energy", 0.0), 4),
            "rhythm": round(features.get("rhythm", 0.0), 4),
            "variation": round(features.get("variation", 0.0), 4),
            "pause": round(features.get("pause", 0.0), 4),
            "spectral": round(features.get("spectral", 0.0), 4),
            "speech_rate": round(features.get("speech_rate", 0.0), 4),
            "duration": round(features.get("duration", 0.0), 2),
        },
        "mapping_version": "v1",
    }


def _point(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    return cx + math.cos(angle) * radius, cy + math.sin(angle) * radius


def render_svg(features: dict[str, float], theme: str, artwork_id: str | None = None) -> tuple[str, str, dict]:
    """Generate SVG artwork and return (artwork_id, data_uri, visual_parameters)."""
    artwork_id = artwork_id or str(uuid.uuid4())
    params = build_visual_parameters(features, theme)
    resolved_theme = params["theme"]
    palette = THEMES[resolved_theme]

    pitch = features.get("pitch", 0.5)
    energy = features.get("energy", 0.5)
    rhythm = features.get("rhythm", 0.5)
    variation = features.get("variation", 0.5)
    pause = features.get("pause", 0.5)
    spectral = features.get("spectral", 0.5)

    seed = int(artwork_id.replace("-", "")[:12], 16)
    rng = random.Random(seed)

    width, height = 1000, 1000
    center_x = width * (0.35 + pitch * 0.3)
    center_y = height * (0.35 + (1 - pitch) * 0.25)
    ring_count = 6 + int(rhythm * 10)
    base_radius = 100 + energy * 250
    wobble = 15 + variation * 120
    line_width = 2 + energy * 8

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" role="img">',
        f'<title>{html.escape("reotoi voice artwork · " + resolved_theme)}</title>',
        f'<metadata id="reotoi-metadata">{html.escape(json.dumps({"artwork_id": artwork_id, **params}))}</metadata>',
        f'<rect width="1000" height="1000" fill="{palette["background"]}"/>',
    ]

    # Voice energy controls the density and scale of nested forms.
    for i in range(ring_count):
        t = i / max(1, ring_count - 1)
        radius = base_radius + i * (25 + energy * 55)
        points = []
        count = 16 + int(spectral * 20)
        for j in range(count):
            angle = (j / count) * math.tau
            local_wobble = math.sin(angle * (2 + rhythm * 6) + t * 3) * wobble * (0.25 + t)
            noise = rng.uniform(-wobble * 0.15, wobble * 0.15)
            r = max(20, radius + local_wobble + noise)
            x, y = _point(center_x, center_y, r, angle)
            points.append(f"{x:.1f},{y:.1f}")
        stroke = palette["strokes"][i % len(palette["strokes"])]
        opacity = 0.28 + (1 - t) * 0.55
        parts.append(
            f'<polygon points="{" ".join(points)}" fill="none" stroke="{stroke}" '
            f'stroke-width="{line_width * (1 - t * 0.4):.2f}" opacity="{opacity:.3f}"/>'
        )

    # Pauses become deliberate negative-space axes.
    gap = 60 + pause * 260
    for i, stroke in enumerate(palette["strokes"]):
        x1 = 100 + i * 80
        x2 = width - x1
        y = center_y + (i - 1) * gap / 3
        parts.append(
            f'<path d="M {x1:.1f} {y:.1f} Q {center_x:.1f} {y - 90 * (0.5 + variation):.1f} '
            f'{x2:.1f} {y:.1f}" fill="none" stroke="{stroke}" stroke-width="{max(1, line_width / 2):.2f}" opacity="0.25"/>'
        )

    parts.append(
        f'<circle cx="{center_x:.1f}" cy="{center_y:.1f}" r="{35 + energy * 40:.1f}" '
        f'fill="{palette["strokes"][0]}" opacity="{0.35 + spectral * 0.35:.3f}"/>'
    )
    parts.append("</svg>")

    svg = "".join(parts)
    return artwork_id, "data:image/svg+xml;charset=utf-8," + quote(svg), params
