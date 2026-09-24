"""Outline icon set for VisionCore.

One icon language: thin outline strokes on a square grid, drawn procedurally
into cached alpha surfaces. No emoji, no bitmap assets, no dependencies - and
because every icon is cached by ``(name, size, colour)`` a frame of icons costs
one blit each.

Icons are drawn inside a square of the requested size with padding, so they stay
aligned at any size. Stroke width scales with the icon size but never drops
below one pixel.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

import pygame

Color = Tuple[int, int, int]

_CACHE: Dict[Tuple[str, int, Color], pygame.Surface] = {}


def icon(name: str, size: int, color: Color) -> pygame.Surface:
    """Return a cached icon surface for the requested name, size and colour."""
    size = max(8, int(size))
    key = (name, size, color)
    surface = _CACHE.get(key)
    if surface is None:
        surface = pygame.Surface((size, size), pygame.SRCALPHA)
        _draw(name, surface, size, color)
        _CACHE[key] = surface
        if len(_CACHE) > 256:
            _CACHE.clear()
            _CACHE[key] = surface
    return surface


# --------------------------------------------------------------------------- #
# Internal drawing
# --------------------------------------------------------------------------- #


def _stroke(size: int) -> int:
    return max(1, round(size / 11.0))


def _draw(name: str, surface: pygame.Surface, s: int, color: Color) -> None:
    """Dispatch one icon onto its square surface."""
    draw = _ICONS.get(name)
    if draw is not None:
        draw(surface, s, color)


def _line(surf, s, color, x1, y1, x2, y2, w=None):
    pygame.draw.line(
        surf, color,
        (x1 * s, y1 * s), (x2 * s, y2 * s), w or _stroke(s),
    )


def _circle(surf, s, color, x, y, r, width=0):
    pygame.draw.circle(surf, color, (x * s, y * s), r * s,
                       width if width else 0)


def _rect(surf, s, color, x, y, w, h, radius=0, outline=0):
    rect = pygame.Rect(x * s, y * s, w * s, h * s)
    pygame.draw.rect(surf, color, rect, outline or 0, border_radius=int(radius * s))


def _poly(surf, s, color, points, outline=0):
    pygame.draw.polygon(
        surf, color, [(px * s, py * s) for px, py in points], outline or 0
    )


def _arc(surf, s, color, x, y, w, h, start, end):
    rect = pygame.Rect(x * s, y * s, w * s, h * s)
    pygame.draw.arc(surf, color, rect, start, end, _stroke(s))


def _octagon(cx, cy, r):
    points = []
    for index in range(8):
        angle = math.pi / 8.0 + index * math.pi / 4.0
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


# -- navigation -------------------------------------------------------------- #


def _eye(surf, s, color):
    w = _stroke(s)
    pygame.draw.ellipse(surf, color, (0.08 * s, 0.28 * s, 0.84 * s, 0.44 * s), w)
    _circle(surf, s, color, 0.5, 0.5, 0.11, w)


def _spark(surf, s, color):
    w = _stroke(s)
    cx, cy = 0.5 * s, 0.5 * s
    long, short = 0.36 * s, 0.16 * s
    for dx, dy, length in ((0, -1, long), (0, 1, long), (-1, 0, long), (1, 0, long),
                           (-1, -1, short), (1, -1, short), (-1, 1, short), (1, 1, short)):
        norm = math.hypot(dx, dy)
        pygame.draw.line(
            surf, color, (cx, cy),
            (cx + dx / norm * length, cy + dy / norm * length), w,
        )


def _sliders(surf, s, color):
    w = _stroke(s)
    _line(surf, s, color, 0.12, 0.32, 0.88, 0.32, w)
    _line(surf, s, color, 0.12, 0.68, 0.88, 0.68, w)
    _circle(surf, s, color, 0.34, 0.32, 0.09, w)
    _circle(surf, s, color, 0.66, 0.68, 0.09, w)


def _gear(surf, s, color):
    w = _stroke(s)
    _circle(surf, s, color, 0.5, 0.5, 0.20, w)
    _circle(surf, s, color, 0.5, 0.5, 0.07, w)
    for index in range(8):
        angle = index * math.pi / 4.0
        x1 = 0.5 + 0.28 * math.cos(angle)
        y1 = 0.5 + 0.28 * math.sin(angle)
        x2 = 0.5 + 0.40 * math.cos(angle)
        y2 = 0.5 + 0.40 * math.sin(angle)
        _line(surf, s, color, x1, y1, x2, y2, w)


# -- media & input ----------------------------------------------------------- #


def _mic(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.38, 0.12, 0.24, 0.38, radius=0.11, outline=w)
    _arc(surf, s, color, 0.22, 0.26, 0.56, 0.42, math.pi * 0.15, math.pi * 0.85)
    _line(surf, s, color, 0.5, 0.68, 0.5, 0.82, w)
    _line(surf, s, color, 0.34, 0.86, 0.66, 0.86, w)


def _send(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.10, 0.18), (0.90, 0.48), (0.10, 0.84), (0.26, 0.50)], w)


def _pause(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.38, 0.22, 0.38, 0.78, w)
    _line(surf, s, color, 0.62, 0.22, 0.62, 0.78, w)


def _play(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.34, 0.20), (0.78, 0.5), (0.34, 0.80)], w)


def _play_pause(surf, s, color):
    w = max(2, _stroke(s))
    _poly(surf, s, color, [(0.16, 0.26), (0.48, 0.5), (0.16, 0.74)], w)
    _line(surf, s, color, 0.62, 0.26, 0.62, 0.74, w)
    _line(surf, s, color, 0.82, 0.26, 0.82, 0.74, w)


def _stop(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, _octagon(0.5, 0.5, 0.40), w)
    _rect(surf, s, color, 0.38, 0.38, 0.24, 0.24)


def _power(surf, s, color):
    w = _stroke(s)
    _arc(surf, s, color, 0.20, 0.20, 0.60, 0.60, math.pi * 0.35, math.pi * 1.65)
    _line(surf, s, color, 0.5, 0.14, 0.5, 0.52, w)


def _mouse(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [
        (0.32, 0.14), (0.32, 0.78), (0.46, 0.63),
        (0.55, 0.86), (0.64, 0.82), (0.55, 0.60), (0.70, 0.59),
    ], w)


def _monitor(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.12, 0.16, 0.76, 0.56, radius=0.06, outline=w)
    _line(surf, s, color, 0.5, 0.72, 0.5, 0.84, w)
    _line(surf, s, color, 0.30, 0.88, 0.70, 0.88, w)


def _camera(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.10, 0.26, 0.80, 0.56, radius=0.10, outline=w)
    _circle(surf, s, color, 0.5, 0.54, 0.16, w)
    _rect(surf, s, color, 0.36, 0.14, 0.28, 0.12, radius=0.05, outline=w)


def _refresh(surf, s, color):
    _arc(surf, s, color, 0.14, 0.14, 0.72, 0.72, -math.pi * 0.4, math.pi * 1.05)
    head = (0.5 + 0.36 * math.cos(math.pi * 1.05), 0.5 + 0.36 * math.sin(math.pi * 1.05))
    pygame.draw.polygon(
        surf, color,
        [(head[0] * s + 0.05 * s, head[1] * s),
         (head[0] * s - 0.02 * s, head[1] * s - 0.09 * s),
         (head[0] * s - 0.09 * s, head[1] * s + 0.02 * s)],
    )


def _volume(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.14, 0.38), (0.32, 0.38), (0.50, 0.20),
                           (0.50, 0.80), (0.32, 0.62), (0.14, 0.62)], w)
    _arc(surf, s, color, 0.52, 0.30, 0.36, 0.40, -math.pi * 0.4, math.pi * 0.4)
    _arc(surf, s, color, 0.52, 0.16, 0.64, 0.68, -math.pi * 0.35, math.pi * 0.35)


def _mute(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.14, 0.38), (0.32, 0.38), (0.50, 0.20),
                           (0.50, 0.80), (0.32, 0.62), (0.14, 0.62)], w)
    _line(surf, s, color, 0.60, 0.36, 0.88, 0.64, w)
    _line(surf, s, color, 0.88, 0.36, 0.60, 0.64, w)


def _brightness(surf, s, color):
    w = _stroke(s)
    _circle(surf, s, color, 0.5, 0.5, 0.20, w)
    for index in range(8):
        angle = index * math.pi / 4.0
        x1 = 0.5 + 0.30 * math.cos(angle)
        y1 = 0.5 + 0.30 * math.sin(angle)
        x2 = 0.5 + 0.40 * math.cos(angle)
        y2 = 0.5 + 0.40 * math.sin(angle)
        _line(surf, s, color, x1, y1, x2, y2, w)


def _skip_next(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.18, 0.24), (0.54, 0.5), (0.18, 0.76)], w)
    _line(surf, s, color, 0.72, 0.24, 0.72, 0.76, w)


def _skip_prev(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.82, 0.24), (0.46, 0.5), (0.82, 0.76)], w)
    _line(surf, s, color, 0.28, 0.24, 0.28, 0.76, w)


def _chevron_up(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.24, 0.62, 0.5, 0.34, w)
    _line(surf, s, color, 0.5, 0.34, 0.76, 0.62, w)


def _chevron_down(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.24, 0.38, 0.5, 0.66, w)
    _line(surf, s, color, 0.5, 0.66, 0.76, 0.38, w)


def _chevron_right(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.38, 0.24, 0.66, 0.5, w)
    _line(surf, s, color, 0.66, 0.5, 0.38, 0.76, w)


def _minimize(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.24, 0.66, 0.76, 0.66, w)


def _maximize(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.22, 0.22, 0.56, 0.56, radius=0.05, outline=w)


def _windows(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.14, 0.20, 0.52, 0.44, radius=0.05, outline=w)
    _rect(surf, s, color, 0.34, 0.36, 0.52, 0.44, radius=0.05, outline=w)


def _app(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.16, 0.16, 0.28, 0.28, radius=0.07, outline=w)
    _rect(surf, s, color, 0.56, 0.16, 0.28, 0.28, radius=0.07, outline=w)
    _rect(surf, s, color, 0.16, 0.56, 0.28, 0.28, radius=0.07, outline=w)
    _rect(surf, s, color, 0.56, 0.56, 0.28, 0.28, radius=0.07, outline=w)


def _hand(surf, s, color):
    w = _stroke(s)
    _rect(surf, s, color, 0.34, 0.36, 0.32, 0.48, radius=0.10, outline=w)
    for x in (0.38, 0.50, 0.62):
        _line(surf, s, color, x, 0.36, x, 0.16, w)
    _line(surf, s, color, 0.34, 0.52, 0.20, 0.40, w)
    _circle(surf, s, color, 0.19, 0.38, 0.05, w)


def _shield(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [
        (0.5, 0.10), (0.86, 0.24), (0.86, 0.52),
        (0.5, 0.90), (0.14, 0.52), (0.14, 0.24),
    ], w)
    _line(surf, s, color, 0.34, 0.48, 0.47, 0.62, w)
    _line(surf, s, color, 0.47, 0.62, 0.68, 0.36, w)


def _info(surf, s, color):
    w = _stroke(s)
    _circle(surf, s, color, 0.5, 0.5, 0.38, w)
    _circle(surf, s, color, 0.5, 0.32, 0.045)
    _line(surf, s, color, 0.5, 0.46, 0.5, 0.70, w)


def _warning(surf, s, color):
    w = _stroke(s)
    _poly(surf, s, color, [(0.5, 0.12), (0.90, 0.84), (0.10, 0.84)], w)
    _line(surf, s, color, 0.5, 0.38, 0.5, 0.62, w)
    _circle(surf, s, color, 0.5, 0.73, 0.04)


def _clock(surf, s, color):
    w = _stroke(s)
    _circle(surf, s, color, 0.5, 0.5, 0.36, w)
    _line(surf, s, color, 0.5, 0.30, 0.5, 0.52, w)
    _line(surf, s, color, 0.5, 0.52, 0.66, 0.62, w)


def _check(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.20, 0.52, 0.42, 0.74, w)
    _line(surf, s, color, 0.42, 0.74, 0.82, 0.28, w)


def _close(surf, s, color):
    w = max(2, _stroke(s))
    _line(surf, s, color, 0.26, 0.26, 0.74, 0.74, w)
    _line(surf, s, color, 0.74, 0.26, 0.26, 0.74, w)


_ICONS = {
    "eye": _eye,
    "spark": _spark,
    "sliders": _sliders,
    "gear": _gear,
    "mic": _mic,
    "send": _send,
    "pause": _pause,
    "play": _play,
    "play_pause": _play_pause,
    "stop": _stop,
    "power": _power,
    "mouse": _mouse,
    "monitor": _monitor,
    "camera": _camera,
    "refresh": _refresh,
    "volume": _volume,
    "mute": _mute,
    "brightness": _brightness,
    "skip_next": _skip_next,
    "skip_prev": _skip_prev,
    "chevron_up": _chevron_up,
    "chevron_down": _chevron_down,
    "chevron_right": _chevron_right,
    "minimize": _minimize,
    "maximize": _maximize,
    "windows": _windows,
    "app": _app,
    "hand": _hand,
    "shield": _shield,
    "info": _info,
    "warning": _warning,
    "clock": _clock,
    "check": _check,
    "close": _close,
}
