"""VisionCore design system: palette, typography, and drawing primitives.

One visual language for the whole application. The rules here are deliberate:

* a restrained, dark-first palette - near-black background, charcoal surfaces,
  soft white text and a single calm cyan-blue accent. Amber is reserved for
  warnings, red for critical states, and both are used sparingly;
* status colour is never the only signal: anything coloured also carries text;
* every primitive draws with plain rectangles, lines and cached surfaces, so a
  full frame costs no per-pixel work and no new allocations beyond small
  text surfaces;
* corners are rounded, borders are thin, and translucency is reserved for the
  small surfaces that float over the camera feed.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.ai.types import AIStatus
from app.controls.safety import ControlState
from app.state import SubsystemState
from app.voice.types import VoiceState

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #

# Base surfaces.
COLOR_BG = (11, 12, 14)            # near-black application background
COLOR_SURFACE = (19, 21, 24)       # charcoal panels and cards
COLOR_SURFACE_RAISED = (25, 28, 32)  # graphite: hover fills, secondary surfaces
COLOR_WELL = (7, 8, 10)            # camera well, darker than the page
COLOR_BORDER = (37, 40, 46)        # subtle hairline borders
COLOR_BORDER_STRONG = (56, 61, 69)  # emphasised borders (hover, focus)

# Text.
COLOR_TEXT = (232, 234, 237)       # soft white
COLOR_TEXT_DIM = (156, 162, 170)   # secondary text
COLOR_TEXT_FAINT = (108, 113, 121)  # tertiary text, fine print

# Accent: a single calm cyan-blue.
COLOR_ACCENT = (98, 170, 220)
COLOR_ACCENT_STRONG = (126, 194, 240)
COLOR_ACCENT_TINT = (23, 32, 40)   # accent-tinted fill for selected states

# Status colours.
COLOR_SUCCESS = (104, 186, 126)    # muted green: active, online
COLOR_WARNING = (222, 170, 92)     # amber: standby, caution
COLOR_DANGER = (226, 104, 104)     # red: error, emergency
COLOR_DANGER_TINT = (42, 24, 26)   # red-tinted fill
COLOR_DISABLED = (100, 105, 112)   # slate: off, unavailable
COLOR_WARNING_TINT = (40, 33, 22)  # amber-tinted fill

# Secondary accent for a second tracked hand (kept quiet).
COLOR_HAND_SECONDARY = (140, 178, 214)

# Translucent surface used by overlays floating above the camera feed.
GLASS_FILL = (12, 14, 17, 208)
GLASS_BORDER = (44, 50, 58, 220)

# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

RADIUS = 12          # panels and cards
RADIUS_SMALL = 8     # buttons and small controls
RADIUS_PILL = 999    # fully rounded chips

TOP_BAR_HEIGHT = 52
NAV_WIDTH = 76

# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #

_SANS_CANDIDATES = ("helvetica", "segoeui", "dejavusans", "arial", "verdana")
_MONO_CANDIDATES = ("menlo", "consolas", "dejavusansmono", "liberationmono", "couriernew")


def _system_font(candidates: Sequence[str], size: int, bold: bool = False) -> pygame.font.Font:
    """Best available system font, falling back to pygame's default."""
    for name in candidates:
        try:
            font = pygame.font.SysFont(name, size, bold=bold)
            if font is not None:
                return font
        except Exception:
            continue
    font = pygame.font.Font(None, size)
    font.set_bold(bold)
    return font


def build_fonts() -> Dict[str, pygame.font.Font]:
    """The application type scale: one family, six sizes, two weights.

    Sentence case is the default voice; mono is reserved for measured values.
    """
    return {
        "display": _system_font(_SANS_CANDIDATES, 26, bold=True),
        "title": _system_font(_SANS_CANDIDATES, 19),
        "heading": _system_font(_SANS_CANDIDATES, 16, bold=True),
        "body": _system_font(_SANS_CANDIDATES, 15),
        "caption": _system_font(_SANS_CANDIDATES, 13),
        "small": _system_font(_SANS_CANDIDATES, 12),
        "mono": _system_font(_MONO_CANDIDATES, 13),
        "mono_small": _system_font(_MONO_CANDIDATES, 12),
    }


# --------------------------------------------------------------------------- #
# Status colours (always paired with a text label by the caller)
# --------------------------------------------------------------------------- #


def subsystem_status_color(status: SubsystemState) -> Tuple[int, int, int]:
    """Theme colour for a subsystem status."""
    if status in (SubsystemState.ONLINE, SubsystemState.ACTIVE):
        return COLOR_SUCCESS
    if status in (SubsystemState.STANDBY, SubsystemState.CHECKING,
                  SubsystemState.INITIALIZING, SubsystemState.LOST):
        return COLOR_WARNING
    if status is SubsystemState.SEARCHING:
        return COLOR_ACCENT
    if status is SubsystemState.DISABLED:
        return COLOR_DISABLED
    return COLOR_DANGER


def ai_status_color(status: AIStatus) -> Tuple[int, int, int]:
    """Colour for a real assistant status."""
    if status is AIStatus.READY:
        return COLOR_SUCCESS
    if status is AIStatus.NOT_CONFIGURED:
        return COLOR_DISABLED
    if status is AIStatus.THINKING:
        return COLOR_ACCENT
    if status is AIStatus.RESPONDING:
        return COLOR_ACCENT_STRONG
    if status is AIStatus.EXECUTING:
        return COLOR_WARNING
    return COLOR_DANGER


def voice_state_color(state: VoiceState) -> Tuple[int, int, int]:
    """Colour for a real microphone state.

    OFF and UNAVAILABLE stay quiet: with the microphone closed the interface
    must not look like it is listening.
    """
    if state is VoiceState.LISTENING:
        return COLOR_WARNING
    if state is VoiceState.PROCESSING:
        return COLOR_ACCENT
    if state is VoiceState.READY:
        return COLOR_SUCCESS
    if state is VoiceState.ERROR:
        return COLOR_DANGER
    return COLOR_DISABLED


def control_state_color(state: ControlState) -> Tuple[int, int, int]:
    """Colour for a control-layer state."""
    if state is ControlState.EMERGENCY_STOP:
        return COLOR_DANGER
    if state is ControlState.ACTIVE:
        return COLOR_SUCCESS
    if state in (ControlState.ARMED, ControlState.PAUSED):
        return COLOR_WARNING
    return COLOR_DISABLED


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def wrap(text: str, font: pygame.font.Font, width: int) -> list:
    """Wrap plain text to a pixel width, breaking overlong words."""
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else f"{current} {word}"
            if font.size(candidate)[0] <= width or not current:
                current = candidate
                continue
            lines.append(current)
            current = word
            while font.size(current)[0] > width and len(current) > 1:
                cut = len(current) - 1
                while cut > 1 and font.size(current[:cut])[0] > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines or [""]


def fit(text: str, font: pygame.font.Font, width: int) -> str:
    """Shorten one line until it fits, with an ellipsis."""
    if font.size(text)[0] <= width:
        return text
    trimmed = text
    while trimmed and font.size(trimmed + "…")[0] > width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# --------------------------------------------------------------------------- #
# Drawing primitives
# --------------------------------------------------------------------------- #


def draw_panel(
    surface: pygame.Surface,
    rect: pygame.Rect,
    fill: Tuple[int, int, int] = COLOR_SURFACE,
    border: Optional[Tuple[int, int, int]] = COLOR_BORDER,
    radius: int = RADIUS,
    border_width: int = 1,
) -> None:
    """Rounded panel with a thin border - the base container."""
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    if border is not None and border_width > 0:
        pygame.draw.rect(surface, border, rect, border_width, border_radius=radius)


def draw_dot(
    surface: pygame.Surface,
    center: Tuple[int, int],
    color: Tuple[int, int, int],
    radius: int = 3,
    ring: bool = False,
) -> None:
    """Status dot; ``ring`` adds a quiet halo used for live states."""
    pygame.draw.circle(surface, color, (int(center[0]), int(center[1])), radius)
    if ring:
        pygame.draw.circle(
            surface, color, (int(center[0]), int(center[1])), radius + 3, 1
        )


def draw_button(
    surface: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    fonts: Dict[str, pygame.font.Font],
    kind: str = "default",
    enabled: bool = True,
    hovered: bool = False,
    icon: Optional[str] = None,
    icon_module=None,
) -> None:
    """A button in the application's visual language.

    ``kind`` is one of ``default``, ``primary``, ``danger``, ``success`` or
    ``ghost``. Disabled buttons keep their geometry but are clearly inert.
    """
    if not enabled:
        fill, border, text_color = COLOR_SURFACE, COLOR_BORDER, COLOR_DISABLED
    elif kind == "primary":
        fill = COLOR_ACCENT_TINT if not hovered else (30, 43, 54)
        border = COLOR_ACCENT if not hovered else COLOR_ACCENT_STRONG
        text_color = COLOR_ACCENT_STRONG if not hovered else COLOR_TEXT
    elif kind == "danger":
        fill = COLOR_DANGER_TINT if not hovered else (56, 30, 32)
        border = COLOR_DANGER
        text_color = COLOR_DANGER if not hovered else (240, 168, 168)
    elif kind == "success":
        fill = (20, 33, 26) if not hovered else (26, 43, 34)
        border = COLOR_SUCCESS
        text_color = COLOR_SUCCESS if not hovered else (178, 224, 192)
    elif kind == "ghost":
        fill = COLOR_SURFACE_RAISED if hovered else None
        border = None
        text_color = COLOR_TEXT_DIM if not hovered else COLOR_TEXT
    else:
        fill = COLOR_SURFACE_RAISED if hovered else COLOR_SURFACE
        border = COLOR_BORDER_STRONG if hovered else COLOR_BORDER
        text_color = COLOR_TEXT if hovered else COLOR_TEXT_DIM

    if fill is not None:
        pygame.draw.rect(surface, fill, rect, border_radius=RADIUS_SMALL)
    if border is not None:
        pygame.draw.rect(surface, border, rect, 1, border_radius=RADIUS_SMALL)

    text_surface = fonts["caption"].render(label, True, text_color)
    if icon is not None and icon_module is not None:
        size = 16
        icon_surface = icon_module.icon(icon, size, text_color)
        gap = 7
        total = icon_surface.get_width() + (gap + text_surface.get_width() if label else 0)
        x = rect.centerx - total // 2
        surface.blit(icon_surface, (x, rect.centery - icon_surface.get_height() // 2))
        if label:
            surface.blit(
                text_surface,
                (x + icon_surface.get_width() + gap,
                 rect.centery - text_surface.get_height() // 2),
            )
    else:
        surface.blit(
            text_surface,
            (rect.centerx - text_surface.get_width() // 2,
             rect.centery - text_surface.get_height() // 2),
        )


def draw_segmented(
    surface: pygame.Surface,
    rect: pygame.Rect,
    options: Sequence[str],
    active_index: int,
    fonts: Dict[str, pygame.font.Font],
    hovered_index: int = -1,
    enabled: bool = True,
) -> list:
    """Segmented control; returns the hit rectangle of each option."""
    pygame.draw.rect(surface, COLOR_WELL, rect, border_radius=RADIUS_SMALL)
    pygame.draw.rect(surface, COLOR_BORDER, rect, 1, border_radius=RADIUS_SMALL)
    count = max(1, len(options))
    cell = rect.width // count
    hits = []
    for index, option in enumerate(options):
        cell_rect = pygame.Rect(rect.left + index * cell, rect.top, cell, rect.height)
        # The last cell absorbs rounding remainder.
        if index == count - 1:
            cell_rect.width = rect.right - cell_rect.left
        hits.append(cell_rect)
        active = index == active_index
        hover = index == hovered_index
        if active and enabled:
            fill_rect = cell_rect.inflate(-3, -3)
            pygame.draw.rect(surface, COLOR_ACCENT_TINT, fill_rect, border_radius=6)
            pygame.draw.rect(surface, COLOR_ACCENT, fill_rect, 1, border_radius=6)
            color = COLOR_ACCENT_STRONG
        elif hover and enabled:
            color = COLOR_TEXT
        elif not enabled:
            color = COLOR_DISABLED
        else:
            color = COLOR_TEXT_DIM
        text = fonts["caption"].render(option, True, color)
        surface.blit(
            text,
            (cell_rect.centerx - text.get_width() // 2,
             cell_rect.centery - text.get_height() // 2),
        )
    return hits


def draw_switch(
    surface: pygame.Surface,
    rect: pygame.Rect,
    on: bool,
    hovered: bool = False,
    enabled: bool = True,
) -> None:
    """A settings toggle. Geometry mirrors a standard desktop switch."""
    track_radius = rect.height // 2
    if not enabled:
        track, knob = COLOR_SURFACE_RAISED, COLOR_DISABLED
    elif on:
        track = COLOR_ACCENT
        knob = (12, 16, 20)
    else:
        track = COLOR_BORDER_STRONG if hovered else COLOR_BORDER
        knob = COLOR_TEXT_DIM
    pygame.draw.rect(surface, track, rect, border_radius=track_radius)
    knob_diameter = rect.height - 6
    knob_x = rect.right - knob_diameter - 3 if on else rect.left + 3
    pygame.draw.circle(
        surface, knob, (knob_x + knob_diameter // 2, rect.centery), knob_diameter // 2
    )


def draw_scrollbar(
    surface: pygame.Surface,
    area: pygame.Rect,
    content_height: float,
    scroll: float,
) -> None:
    """Thin scrollbar for a scrollable region."""
    if content_height <= area.height + 1:
        return
    track = max(20, area.height - 4)
    thumb = max(24, int(track * area.height / content_height))
    max_scroll = content_height - area.height
    fraction = 1.0 - min(1.0, max(0.0, scroll / max_scroll))
    top = area.top + 2 + int((track - thumb) * fraction)
    pygame.draw.rect(
        surface, COLOR_BORDER_STRONG, (area.right - 3, top, 3, thumb), border_radius=2
    )


def corner_patches(radius: int, fill: Tuple[int, int, int]) -> Tuple:
    """Four corner patches that give a blitted video frame rounded corners.

    Each patch is a small square in the surrounding surface colour with a
    transparent quarter-disc where the video shows through. Created once per
    radius and cached; blitting four of them per frame is negligible.
    """
    key = ("patch", radius, fill)
    cache = _SURFACE_CACHE.get(key)
    if cache is None:
        size = radius + 1
        patch = pygame.Surface((size, size), pygame.SRCALPHA)
        patch.fill((*fill, 255))
        pygame.draw.circle(patch, (0, 0, 0, 0), (radius, radius), radius)
        flipped = (
            patch,
            pygame.transform.flip(patch, True, False),
            pygame.transform.flip(patch, False, True),
            pygame.transform.flip(patch, True, True),
        )
        _SURFACE_CACHE[key] = flipped
        if len(_SURFACE_CACHE) > 32:
            _SURFACE_CACHE.clear()
            _SURFACE_CACHE[key] = flipped
        return flipped
    return cache


def round_video(
    surface: pygame.Surface,
    video_rect: pygame.Rect,
    radius: int,
    fill: Tuple[int, int, int],
) -> None:
    """Make a blitted video rectangle appear rounded at its corners."""
    top_left, top_right, bottom_left, bottom_right = corner_patches(radius, fill)
    surface.blit(top_left, video_rect.topleft)
    surface.blit(top_right, (video_rect.right - top_right.get_width(), video_rect.top))
    surface.blit(bottom_left, (video_rect.left, video_rect.bottom - bottom_left.get_height()))
    surface.blit(
        bottom_right,
        (video_rect.right - bottom_right.get_width(),
         video_rect.bottom - bottom_right.get_height()),
    )


def glass_pill(
    size: Tuple[int, int],
) -> pygame.Surface:
    """A cleared translucent rounded surface for overlays above the video."""
    width = max(1, size[0])
    height = max(1, size[1])
    key = ("glass", width, height)
    pill = _SURFACE_CACHE.get(key)
    if pill is None:
        pill = pygame.Surface((width, height), pygame.SRCALPHA)
        pill.fill(GLASS_FILL)
        pygame.draw.rect(pill, GLASS_BORDER, pill.get_rect(), 1, border_radius=8)
        _SURFACE_CACHE[key] = pill
        if len(_SURFACE_CACHE) > 48:
            _SURFACE_CACHE.clear()
            _SURFACE_CACHE[key] = pill
    else:
        pill.fill((0, 0, 0, 0))
        pill.fill(GLASS_FILL)
        pygame.draw.rect(pill, GLASS_BORDER, pill.get_rect(), 1, border_radius=8)
    return pill


_SURFACE_CACHE: Dict[tuple, pygame.Surface] = {}
