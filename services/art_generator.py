"""Procedural SVG artwork generation for reotoi.

The renderer maps the normalized voice feature vector into a bounded 800x800
composition. Every drawing operation is clipped to an inner artboard so the
artwork cannot spill outside the visible frame. Fixed artwork IDs produce
reproducible artwork for a resolved theme.
"""

from __future__ import annotations
import html
import json
import math
import random
import uuid
from urllib.parse import quote

CANVAS_SIZE = 800
ART_MARGIN = 48
ART_SIZE = CANVAS_SIZE - (ART_MARGIN * 2)

THEMES = {
    "abstract": {
        "backgrounds": ["#0f1720", "#151922", "#1b1b20", "#20242a"],
        "strokes": ["#f3efe5", "#8fb8a8", "#d6a85d", "#b9c7d4", "#d8b4a0"],
        "accents": ["#f3efe5", "#d6a85d", "#b9c7d4"],
    },
    "nature": {
        "backgrounds": ["#102018", "#14251d", "#1b261e", "#20281f"],
        "strokes": ["#c8d7bd", "#82a878", "#d2b48c", "#a9c4a0", "#d9c9a8"],
        "accents": ["#d2b48c", "#c8d7bd", "#a9c4a0"],
    },
    "cosmos": {
        "backgrounds": ["#11101f", "#17152a", "#1c1930", "#121625"],
        "strokes": ["#d9d2ff", "#8e9be8", "#e5bd78", "#b9b4e8", "#c8d7f0"],
        "accents": ["#d9d2ff", "#e5bd78", "#c8d7f0"],
    },
    "architecture": {
        "backgrounds": ["#161616", "#1c1c1c", "#202020", "#171a1d"],
        "strokes": ["#e8e2d6", "#9ca6b4", "#c79559", "#c8c5bd", "#b4bec8"],
        "accents": ["#e8e2d6", "#c79559", "#b4bec8"],
    },
    "organic": {
        "backgrounds": ["#191915", "#202018", "#24221b", "#1b1d19"],
        "strokes": ["#d9d0b8", "#a8b58e", "#cf9d77", "#c5c9a8", "#d8bda5"],
        "accents": ["#d9d0b8", "#cf9d77", "#c5c9a8"],
    },
    "geometric": {
        "backgrounds": ["#10171b", "#151d21", "#192126", "#11191e"],
        "strokes": ["#e6e7e3", "#7aa3ad", "#d29a62", "#b9cbd0", "#d4c5b2"],
        "accents": ["#e6e7e3", "#d29a62", "#b9cbd0"],
    },
}

def build_palette(theme: str, rng: random.Random) -> dict:
    """Create a deterministic randomized palette for the resolved theme."""
    theme_palette = THEMES[theme]

    background = rng.choice(theme_palette["backgrounds"])

    strokes = rng.sample(
        theme_palette["strokes"],
        k=min(3, len(theme_palette["strokes"])),
    )

    accent = rng.choice(theme_palette["accents"])

    return {
        "background": background,
        "strokes": strokes,
        "accent": accent,
    }

def choose_theme(theme: str) -> str:
    """Resolve ``surprise`` to one supported theme."""
    if theme != "surprise":
        return theme
    return random.SystemRandom().choice(sorted(THEMES))


def build_visual_parameters(features: dict[str, float], theme: str) -> dict:
    """Build the explicit voice-to-visual mapping used by the renderer."""
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
        "mapping_version": "v2",
    }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _point(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    return cx + math.cos(angle) * radius, cy + math.sin(angle) * radius


def _polygon_points(cx: float, cy: float, radius: float, sides: int, rotation: float) -> str:
    points = []
    for index in range(sides):
        angle = rotation + (index / sides) * math.tau
        x, y = _point(cx, cy, radius, angle)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _ellipse_path(cx: float, cy: float, rx: float, ry: float, rotation: float) -> str:
    """Return a closed ellipse path with rotation."""
    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)
    points = []
    for index in range(49):
        angle = (index / 48) * math.tau
        x = rx * math.cos(angle)
        y = ry * math.sin(angle)
        px = cx + x * cos_r - y * sin_r
        py = cy + x * sin_r + y * cos_r
        points.append((px, py))

    commands = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for px, py in points[1:]:
        commands.append(f"L {px:.1f} {py:.1f}")
    commands.append("Z")
    return " ".join(commands)


def _wave_path(
    left: float,
    right: float,
    y: float,
    amplitude: float,
    cycles: float,
    phase: float,
    samples: int = 80,
) -> str:
    points = []
    for index in range(samples + 1):
        t = index / samples
        x = left + (right - left) * t
        wave = math.sin((t * cycles * math.tau) + phase) * amplitude
        points.append((x, y + wave))

    commands = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for px, py in points[1:]:
        commands.append(f"L {px:.1f} {py:.1f}")
    return " ".join(commands)


def _add_abstract(parts: list[str], features: dict[str, float], palette: dict, rng: random.Random) -> None:
    pitch = _clamp(features.get("pitch", 0.5))
    energy = _clamp(features.get("energy", 0.5))
    rhythm = _clamp(features.get("rhythm", 0.5))
    variation = _clamp(features.get("variation", 0.5))
    pause = _clamp(features.get("pause", 0.5))
    spectral = _clamp(features.get("spectral", 0.5))

    cx = 400 + (pitch - 0.5) * 130
    cy = 395 - (energy - 0.5) * 80
    wave_count = 7 + int(rhythm * 7)

    for index in range(wave_count):
        t = index / max(1, wave_count - 1)
        y = 150 + t * 500
        amplitude = 16 + variation * 70 + t * 22
        cycles = 1.3 + rhythm * 3.8 + t * 1.3
        phase = index * 0.65 + pitch * 2.1
        stroke = palette["strokes"][index % 3]
        width = 2.0 + energy * 4.5 - t * 1.0
        opacity = 0.18 + (1.0 - t) * 0.55
        parts.append(
            f'<path d="{_wave_path(78, 722, y, amplitude, cycles, phase)}" '
            f'fill="none" stroke="{stroke}" stroke-width="{width:.2f}" opacity="{opacity:.3f}"/>'
        )

    ring_count = 4 + int(spectral * 5)
    for index in range(ring_count):
        radius = 80 + index * (36 + energy * 28)
        rotation = rng.uniform(-0.15, 0.15) + variation * 0.6
        rx = min(radius * (1.0 + pitch * 0.45), 300)
        ry = min(radius * (0.64 + pause * 0.4), 260)
        parts.append(
            f'<path d="{_ellipse_path(cx, cy, rx, ry, rotation)}" fill="none" '
            f'stroke="{palette["strokes"][index % 3]}" stroke-width="{1.4 + energy * 2:.2f}" '
            f'opacity="{0.16 + (ring_count - index) / ring_count * 0.34:.3f}"/>'
        )

    parts.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{30 + energy * 38:.1f}" '
        f'fill="{palette["accent"]}" opacity="{0.28 + spectral * 0.4:.3f}"/>'
    )


def _add_nature(parts: list[str], features: dict[str, float], palette: dict, rng: random.Random) -> None:
    energy = _clamp(features.get("energy", 0.5))
    rhythm = _clamp(features.get("rhythm", 0.5))
    variation = _clamp(features.get("variation", 0.5))
    pause = _clamp(features.get("pause", 0.5))

    stem_x = 330 + rng.uniform(-35, 35)
    top_y = 150
    bottom_y = 640
    sway = 45 + variation * 95

    parts.append(
        f'<path d="M {stem_x:.1f} {bottom_y:.1f} C {stem_x - sway:.1f} 500, '
        f'{stem_x + sway:.1f} 320, {stem_x:.1f} {top_y:.1f}" '
        f'fill="none" stroke="{palette["strokes"][1]}" stroke-width="{7 + energy * 6:.1f}" '
        f'opacity="0.8"/>'
    )

    leaf_count = 7 + int(rhythm * 7)
    for index in range(leaf_count):
        t = index / max(1, leaf_count - 1)
        y = bottom_y - t * 420
        side = -1 if index % 2 else 1
        x = stem_x + math.sin(t * math.tau * (1.3 + rhythm)) * sway * 0.45
        leaf_length = 55 + energy * 55 + rng.uniform(-15, 15)
        leaf_height = 20 + variation * 24
        rotation = side * (0.25 + t * 0.8)
        dx = math.cos(rotation) * leaf_length
        dy = math.sin(rotation) * leaf_length
        end_x = x + side * dx
        end_y = y - abs(dy)
        control_x = (x + end_x) / 2
        control_y = y - leaf_height * (1.2 + pause)
        path = (
            f"M {x:.1f} {y:.1f} Q {control_x:.1f} {control_y:.1f} {end_x:.1f} {end_y:.1f} "
            f"Q {control_x:.1f} {y + leaf_height:.1f} {x:.1f} {y:.1f} Z"
        )
        parts.append(
            f'<path d="{path}" fill="none" stroke="{palette["strokes"][index % 3]}" '
            f'stroke-width="{1.8 + energy * 2.2:.2f}" opacity="{0.38 + t * 0.38:.3f}"/>'
        )

    for index in range(3):
        radius = 130 + index * 85 + pause * 70
        parts.append(
            f'<path d="{_ellipse_path(400, 420, radius, radius * 0.45, index * 0.55)}" '
            f'fill="none" stroke="{palette["strokes"][index]}" stroke-width="1.4" opacity="0.25"/>'
        )


def _add_cosmos(parts: list[str], features: dict[str, float], palette: dict, rng: random.Random) -> None:
    pitch = _clamp(features.get("pitch", 0.5))
    energy = _clamp(features.get("energy", 0.5))
    rhythm = _clamp(features.get("rhythm", 0.5))
    variation = _clamp(features.get("variation", 0.5))
    spectral = _clamp(features.get("spectral", 0.5))

    cx = 400 + (pitch - 0.5) * 90
    cy = 400 + (energy - 0.5) * 70

    for _ in range(70):
        x = rng.uniform(80, 720)
        y = rng.uniform(80, 720)
        radius = rng.uniform(0.7, 2.6) * (0.7 + spectral)
        opacity = rng.uniform(0.15, 0.75)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
            f'fill="{palette["strokes"][rng.randrange(3)]}" opacity="{opacity:.3f}"/>'
        )

    orbit_count = 4 + int(rhythm * 4)
    for index in range(orbit_count):
        scale = 1 + index * 0.22
        rx = min(90 * scale + variation * 25, 315)
        ry = min(36 * scale + variation * 16, 190)
        rotation = (index - orbit_count / 2) * 0.27 + pitch * 0.35
        parts.append(
            f'<path d="{_ellipse_path(cx, cy, rx, ry, rotation)}" fill="none" '
            f'stroke="{palette["strokes"][index % 3]}" stroke-width="{1.2 + energy * 2.4:.2f}" '
            f'opacity="{0.2 + (orbit_count-index)/orbit_count*0.36:.3f}"/>'
        )

    core = 28 + energy * 55
    parts.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{core:.1f}" fill="{palette["accent"]}" '
        f'opacity="{0.35 + spectral * 0.35:.3f}"/>'
    )
    parts.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{core * 1.8:.1f}" fill="none" '
        f'stroke="{palette["strokes"][1]}" stroke-width="2" opacity="0.24"/>'
    )


def _add_architecture(parts: list[str], features: dict[str, float], palette: dict, rng: random.Random) -> None:
    pitch = _clamp(features.get("pitch", 0.5))
    energy = _clamp(features.get("energy", 0.5))
    rhythm = _clamp(features.get("rhythm", 0.5))
    pause = _clamp(features.get("pause", 0.5))
    spectral = _clamp(features.get("spectral", 0.5))

    left, right = 92, 708
    top, bottom = 92, 708
    spacing = max(42, 74 - rhythm * 24)

    for x in range(int(left), int(right + 1), int(spacing)):
        parts.append(
            f'<path d="M {x:.1f} {top:.1f} L {x + (pitch - 0.5) * 90:.1f} {bottom:.1f}" '
            f'fill="none" stroke="{palette["strokes"][1]}" stroke-width="1.2" opacity="0.22"/>'
        )

    for y in range(int(top), int(bottom + 1), int(spacing)):
        parts.append(
            f'<path d="M {left:.1f} {y:.1f} L {right:.1f} {y + (pitch - 0.5) * 45:.1f}" '
            f'fill="none" stroke="{palette["strokes"][0]}" stroke-width="1.1" opacity="0.2"/>'
        )

    frame_size = 150 + energy * 210
    frame_x = 400 - frame_size / 2
    frame_y = 400 - frame_size / 2
    parts.append(
        f'<rect x="{frame_x:.1f}" y="{frame_y:.1f}" width="{frame_size:.1f}" height="{frame_size:.1f}" '
        f'fill="none" stroke="{palette["accent"]}" stroke-width="{4 + energy * 3:.1f}" opacity="0.7"/>'
    )

    levels = 3 + int(spectral * 4)
    for index in range(levels):
        inset = index * 25
        width = frame_size - inset * 2
        if width <= 40:
            break
        offset = rng.uniform(-pause * 15, pause * 15)
        parts.append(
            f'<rect x="{frame_x + inset + offset:.1f}" y="{frame_y + inset:.1f}" '
            f'width="{width:.1f}" height="{width:.1f}" fill="none" '
            f'stroke="{palette["strokes"][index % 3]}" stroke-width="{1.2 + energy * 1.8:.2f}" '
            f'opacity="{0.22 + (levels-index)/levels*0.32:.3f}"/>'
        )


def _add_organic(parts: list[str], features: dict[str, float], palette: dict, rng: random.Random) -> None:
    pitch = _clamp(features.get("pitch", 0.5))
    energy = _clamp(features.get("energy", 0.5))
    rhythm = _clamp(features.get("rhythm", 0.5))
    variation = _clamp(features.get("variation", 0.5))
    pause = _clamp(features.get("pause", 0.5))

    ribbon_count = 5 + int(rhythm * 5)
    for index in range(ribbon_count):
        center = 150 + index * (500 / max(1, ribbon_count - 1))
        spread = 70 + variation * 80
        phase = rng.uniform(0, math.tau)
        points = []
        for sample in range(70):
            t = sample / 69
            x = 105 + t * 590
            y = center + math.sin(t * math.tau * (0.8 + rhythm * 2.5) + phase) * spread
            y += math.sin(t * math.tau * 2.0 + phase * 0.55) * (18 + pause * 45)
            points.append((x, y))

        upper = [(x, y - (7 + energy * 7)) for x, y in points]
        lower = [(x, y + (7 + energy * 7)) for x, y in reversed(points)]
        polygon = upper + lower
        point_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in polygon)
        parts.append(
            f'<polygon points="{point_text}" fill="none" stroke="{palette["strokes"][index % 3]}" '
            f'stroke-width="{1.5 + energy * 3:.2f}" opacity="{0.2 + (ribbon_count-index)/ribbon_count*0.35:.3f}"/>'
        )

    cx = 400 + (pitch - 0.5) * 100
    cy = 400
    for index in range(4):
        radius = 50 + index * (30 + pause * 35)
        parts.append(
            f'<path d="{_ellipse_path(cx, cy, radius, radius * (0.55 + variation * 0.45), index * 0.45)}" '
            f'fill="none" stroke="{palette["strokes"][index % 3]}" stroke-width="{2 + energy * 2:.2f}" opacity="0.35"/>'
        )


def _add_geometric(parts: list[str], features: dict[str, float], palette: dict, rng: random.Random) -> None:
    pitch = _clamp(features.get("pitch", 0.5))
    energy = _clamp(features.get("energy", 0.5))
    rhythm = _clamp(features.get("rhythm", 0.5))
    variation = _clamp(features.get("variation", 0.5))
    spectral = _clamp(features.get("spectral", 0.5))

    cx = 400 + (pitch - 0.5) * 120
    cy = 400 + (energy - 0.5) * 90
    sides = 5 + int(rhythm * 4)
    layers = 5 + int(spectral * 4)

    for index in range(layers):
        radius = 75 + index * (40 + variation * 32)
        rotation = index * (0.18 + pitch * 0.55) + rng.uniform(-0.06, 0.06)
        parts.append(
            f'<polygon points="{_polygon_points(cx, cy, min(radius, 300), sides, rotation)}" '
            f'fill="none" stroke="{palette["strokes"][index % 3]}" '
            f'stroke-width="{2 + energy * 3.5 - index * 0.15:.2f}" '
            f'opacity="{0.2 + (layers-index)/layers*0.36:.3f}"/>'
        )

    spoke_count = 6 + int(rhythm * 8)
    for index in range(spoke_count):
        angle = (index / spoke_count) * math.tau + variation * 0.5
        inner = 54
        outer = min(300, 220 + spectral * 80)
        x1, y1 = _point(cx, cy, inner, angle)
        x2, y2 = _point(cx, cy, outer, angle)
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{palette["strokes"][index % 3]}" stroke-width="1.5" opacity="0.28"/>'
        )

    parts.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{25 + energy * 35:.1f}" '
        f'fill="{palette["accent"]}" opacity="{0.35 + spectral * 0.25:.3f}"/>'
    )


def render_svg(
    features: dict[str, float],
    theme: str,
    artwork_id: str | None = None,
) -> tuple[str, str, dict]:
    """Generate bounded SVG artwork and return ``(artwork_id, data_uri, parameters)``."""
    artwork_id = artwork_id or str(uuid.uuid4())
    params = build_visual_parameters(features, theme)
    resolved_theme = params["theme"]

    seed = int(artwork_id.replace("-", "")[:12], 16)
    rng = random.Random(seed)

    palette = build_palette(resolved_theme, rng)
    # palette = THEMES[resolved_theme]

    # seed = int(artwork_id.replace("-", "")[:12], 16)
    # rng = random.Random(seed)
    metadata = {
        "artwork_id": artwork_id,
        **params,
        "palette": palette,
    }

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_SIZE}" height="{CANVAS_SIZE}" '
        f'viewBox="0 0 {CANVAS_SIZE} {CANVAS_SIZE}" preserveAspectRatio="xMidYMid meet" role="img">',

        f'<title>{html.escape("reotoi voice artwork · " + resolved_theme)}</title>',

        f'<metadata id="reotoi-metadata">{html.escape(json.dumps(metadata))}</metadata>',

        "<defs>",

        f'<clipPath id="art-bounds">'
        f'<rect x="{ART_MARGIN}" y="{ART_MARGIN}" '
        f'width="{ART_SIZE}" height="{ART_SIZE}" rx="24"/>'
        f'</clipPath>',

        "</defs>",

        # Full canvas background
        f'<rect width="{CANVAS_SIZE}" height="{CANVAS_SIZE}" '
        f'fill="{palette["background"]}"/>',

        # Inner artboard background and border
        f'<rect x="{ART_MARGIN}" y="{ART_MARGIN}" '
        f'width="{ART_SIZE}" height="{ART_SIZE}" rx="24" '
        f'fill="{palette["background"]}" '
        f'stroke="{palette["strokes"][1]}" '
        f'stroke-width="1" opacity="0.9"/>',

        # Clip all generated artwork to the inner artboard
        '<g clip-path="url(#art-bounds)">',
    ]
    # parts = [
    #     f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_SIZE}" height="{CANVAS_SIZE}" '
    #     f'viewBox="0 0 {CANVAS_SIZE} {CANVAS_SIZE}" preserveAspectRatio="xMidYMid meet" role="img">',
    #     f'<title>{html.escape("reotoi voice artwork · " + resolved_theme)}</title>',
    #     f'<metadata id="reotoi-metadata">{html.escape(json.dumps({"artwork_id": artwork_id, **params}))}</metadata>',
    #     "<defs>",
    #     f'<clipPath id="art-bounds"><rect x="{ART_MARGIN}" y="{ART_MARGIN}" width="{ART_SIZE}" height="{ART_SIZE}" rx="24"/></clipPath>',
    #     "</defs>",
    #     f'<rect width="{CANVAS_SIZE}" height="{CANVAS_SIZE}" fill="{palette["background"]}"/>',
    #     f'<rect x="{ART_MARGIN}" y="{ART_MARGIN}" width="{ART_SIZE}" height="{ART_SIZE}" rx="24" '
    #     f'fill="{palette["background"]}" stroke="{palette["strokes"][1]}" stroke-width="1" opacity="0.9"/>',
    #     '<g clip-path="url(#art-bounds)">',
    # ]

    renderers = {
        "abstract": _add_abstract,
        "nature": _add_nature,
        "cosmos": _add_cosmos,
        "architecture": _add_architecture,
        "organic": _add_organic,
        "geometric": _add_geometric,
    }
    renderers[resolved_theme](parts, features, palette, rng)

    parts.extend(["</g>", "</svg>"])
    svg = "".join(parts)
    data_uri = "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")
    return artwork_id, data_uri, params
