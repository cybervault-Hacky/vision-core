"""Central interaction focus: status ring, readout and tracking quality.

This is the visual anchor of the interface. It answers one question at a glance
- what is the system doing right now - using only real state published by the
interaction director:

* a segmented ring whose status word (SEARCHING / TRACKING / READY / ACTIVE /
  PAUSED / EMERGENCY) comes from the control and tracking states;
* an acquisition sweep driven by the tracker's measured lock progress, so the
  ring fills at exactly the rate the lock is stabilising;
* a two line readout that follows the real interaction sequence
  (SCANNING / NO HAND -> HAND DETECTED / ACQUIRING -> TRACKING / LOCKED ->
  GESTURE / PINCH -> ACTION / LEFT CLICK);
* a tracking quality chip built from the tracker state and its measured
  confidence, plus the number of hands actually detected.

Everything is drawn procedurally with cached point sets and cached surfaces, so
a frame costs a few dozen line segments - no full screen effects, no per frame
image work, no animation objects created per frame.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import pygame

from app.hand_tracking import TrackingState
from app.interaction import RingState, TrackingQuality
from app.state import Telemetry
from ui.hud import (
    COLOR_CYAN_PRIMARY,
    COLOR_DISABLED,
    COLOR_ERROR,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BG,
    COLOR_STANDBY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)

# Ring geometry.
RADIUS_RATIO = 0.135
MIN_RADIUS = 44
MAX_RADIUS = 74
SEGMENTS = 8
SEGMENT_SPAN = 0.72          # fraction of each segment that is drawn
SWEEP_SPAN = 0.055           # fraction of the circle covered by the sweep head
RING_POINTS = 10             # points per arc, enough for a smooth small arc

# Readout placement.
READOUT_GAP = 14
CHIP_HEIGHT = 20
CHIP_PADDING = 8

DefColor = Tuple[int, int, int]

# Status word colour per ring state. Emergency and pause are safety states and
# keep their own colour even while another layer is active.
_RING_COLOR: Dict[RingState, DefColor] = {
    RingState.OFFLINE: COLOR_DISABLED,
    RingState.SEARCHING: COLOR_TEXT_MUTED,
    RingState.TRACKING: COLOR_ICE_BLUE,
    RingState.READY: COLOR_CYAN_PRIMARY,
    RingState.ACTIVE: COLOR_ONLINE,
    RingState.PAUSED: COLOR_STANDBY,
    RingState.EMERGENCY: COLOR_ERROR,
}

_QUALITY_COLOR: Dict[TrackingQuality, DefColor] = {
    TrackingQuality.OFFLINE: COLOR_DISABLED,
    TrackingQuality.SEARCHING: COLOR_TEXT_MUTED,
    TrackingQuality.ACQUIRING: COLOR_ICE_BLUE,
    TrackingQuality.LOCKED: COLOR_ONLINE,
    TrackingQuality.LOW_CONFIDENCE: COLOR_STANDBY,
    TrackingQuality.LOST: COLOR_ERROR,
}


class FocusView:
    """Renders the central focus area and the status ring."""

    def __init__(self) -> None:
        self._point_cache: Dict[Tuple[int, int, int, int, int], List[Tuple[int, int]]] = {}
        self._chrome_cache: Dict[Tuple[int, int], pygame.Surface] = {}
        self._chip_cache: Dict[Tuple[str, int], pygame.Surface] = {}

    # -- public API -------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        viewport: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Draw the focus area for the current interaction snapshot."""
        interaction = telemetry.interaction
        centre = (viewport.centerx, viewport.centery)
        radius = self._radius(viewport)
        color = _RING_COLOR.get(interaction.ring, COLOR_TEXT_MUTED)
        activity = interaction.ring_activity

        self._draw_ring(surface, centre, radius, interaction, color, activity)
        self._draw_readout(surface, centre, radius, telemetry, fonts, color)
        self._draw_quality_chip(surface, viewport, telemetry, fonts)

    # -- ring -------------------------------------------------------------- #

    @staticmethod
    def _radius(viewport: pygame.Rect) -> int:
        span = min(viewport.width, viewport.height)
        return int(max(MIN_RADIUS, min(MAX_RADIUS, span * RADIUS_RATIO)))

    def _arc_points(
        self,
        centre: Tuple[int, int],
        radius: int,
        start: float,
        end: float,
    ) -> List[Tuple[int, int]]:
        """Cached arc point set (angles in turns, clockwise from the top)."""
        key = (centre[0], centre[1], radius, int(start * 360), int(end * 360))
        points = self._point_cache.get(key)
        if points is not None:
            return points
        cx, cy = centre
        points = []
        for step in range(RING_POINTS + 1):
            turn = start + (end - start) * (step / RING_POINTS)
            angle = (turn * math.tau) - (math.pi / 2.0)
            points.append(
                (int(cx + radius * math.cos(angle)), int(cy + radius * math.sin(angle)))
            )
        self._point_cache[key] = points
        if len(self._point_cache) > 256:
            self._point_cache.clear()
        return points

    def _draw_ring(
        self,
        surface: pygame.Surface,
        centre: Tuple[int, int],
        radius: int,
        interaction,
        color: DefColor,
        activity: float,
    ) -> None:
        """Segmented ring, sweep head and acquisition progress."""
        alpha = int(70 + 150 * activity)
        arc_width = 2 if interaction.ring is not RingState.ACTIVE else 3

        # Segmented base ring: alternating gaps read as a technical dial rather
        # than a glow, and keep the ring from covering the hand behind it.
        for index in range(SEGMENTS):
            start = index / SEGMENTS
            end = start + (SEGMENT_SPAN / SEGMENTS)
            points = self._arc_points(centre, radius, start, end)
            pygame.draw.lines(
                surface, self._shade(color, alpha), False, points, arc_width
            )

        # Sweep head: one short arc travelling around the dial. Its speed follows
        # the state (fast while acting, slow while only searching) and it is
        # absent when the pipeline is offline.
        if interaction.ring is not RingState.OFFLINE:
            phase = interaction.ring_phase
            points = self._arc_points(centre, radius, phase, phase + SWEEP_SPAN)
            pygame.draw.lines(surface, self._shade(color, 235), False, points, 3)

        # Acquisition progress: the real lock progress of the tracker, drawn as
        # a solid inner arc. Nothing is drawn before any evidence exists.
        progress = interaction.ring_progress
        if 0.0 < progress < 1.0:
            points = self._arc_points(centre, radius - 6, 0.0, progress)
            pygame.draw.lines(surface, self._shade(COLOR_ICE_BLUE, 210), False, points, 2)
        elif progress >= 1.0 and interaction.ring in (RingState.TRACKING, RingState.READY):
            points = self._arc_points(centre, radius - 6, 0.0, 1.0)
            pygame.draw.lines(surface, self._shade(COLOR_ONLINE, 170), False, points, 2)

        # Emergency replaces all decoration with one unbroken, unmistakable ring.
        if interaction.ring is RingState.EMERGENCY:
            pulse = 0.55 + 0.45 * math.sin(interaction.ring_phase * math.tau)
            outer = self._arc_points(centre, radius + 9, 0.0, 1.0)
            pygame.draw.lines(
                surface, self._shade(COLOR_ERROR, int(120 + 110 * pulse)), False, outer, 2
            )
            pygame.draw.lines(
                surface,
                self._shade(COLOR_ERROR, 230),
                False,
                self._arc_points(centre, radius, 0.0, 1.0),
                3,
            )

        # A hand is being observed: mark the four cardinal positions so the
        # reading direction of the dial is unambiguous.
        if interaction.ring in (RingState.TRACKING, RingState.READY, RingState.ACTIVE):
            for turn in (0.0, 0.25, 0.5, 0.75):
                points = self._arc_points(centre, radius + 4, turn, turn + 0.012)
                pygame.draw.lines(surface, self._shade(color, 200), False, points, 2)

    # -- readout ----------------------------------------------------------- #

    def _draw_readout(
        self,
        surface: pygame.Surface,
        centre: Tuple[int, int],
        radius: int,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        color: DefColor,
    ) -> None:
        """Two line focus readout under the ring, animated on each transition."""
        interaction = telemetry.interaction
        focus = interaction.focus
        transition = interaction.transition
        # Small rise-and-settle on state changes: the readout lifts into place
        # instead of snapping, which is what makes transitions readable.
        offset = int(6 * (1.0 - transition))
        alpha = int(90 + 165 * transition)

        y = centre[1] + radius + READOUT_GAP + offset
        headline = fonts["subheading"].render(focus.headline, True, color)
        headline.set_alpha(alpha)
        surface.blit(
            headline, headline.get_rect(center=(centre[0], y + headline.get_height() // 2))
        )

        value = focus.value
        if value:
            # A safety state carries its own colour all the way through the
            # readout; otherwise the value line stays quiet and readable.
            value_color = color if interaction.state.is_safety else COLOR_TEXT_WHITE
            value_surf = fonts["caption"].render(value, True, value_color)
            value_surf.set_alpha(alpha)
            surface.blit(
                value_surf,
                value_surf.get_rect(
                    center=(centre[0], y + headline.get_height() + value_surf.get_height())
                ),
            )

    # -- quality chip ------------------------------------------------------ #

    def _draw_quality_chip(
        self,
        surface: pygame.Surface,
        viewport: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Tracking quality and hand count, both straight from the tracker."""
        interaction = telemetry.interaction
        quality = interaction.quality
        color = _QUALITY_COLOR.get(quality, COLOR_TEXT_MUTED)

        # The project's only confidence model is the tracker's own confidence:
        # it is shown as a percentage when the tracker reports one.
        confidence = interaction.confidence
        parts = [f"TRACKING {quality.label}"]
        if confidence is not None and quality is not TrackingQuality.OFFLINE:
            parts.append(f"CONF {int(round(confidence * 100)):d}%")
        # The hand count is the tracker's own count, so "HANDS 2" appears only
        # when two hands are really being tracked.
        parts.append(f"HANDS {interaction.hands}")
        text = "   ".join(parts)

        key = (text, color[1])
        chip = self._chip_cache.get(key)
        if chip is None:
            label = fonts["mono_small"].render(text, True, color)
            chip = pygame.Surface(
                (label.get_width() + CHIP_PADDING * 2, CHIP_HEIGHT), pygame.SRCALPHA
            )
            chip.fill((*COLOR_PANEL_BG, 205))
            pygame.draw.rect(chip, (*color, 120), chip.get_rect(), 1)
            chip.blit(label, (CHIP_PADDING, (CHIP_HEIGHT - label.get_height()) // 2))
            self._chip_cache[key] = chip
            if len(self._chip_cache) > 32:
                self._chip_cache.clear()

        position = (viewport.left + 12, viewport.bottom - CHIP_HEIGHT - 12)
        surface.blit(chip, position)

        # A hand is tracked: a short tick under the chip marks the live region.
        if telemetry.tracking_state is TrackingState.TRACKING:
            tick = pygame.Rect(position[0], position[1] - 5, chip.get_width(), 2)
            pygame.draw.rect(surface, (*COLOR_ONLINE, 180), tick)

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _shade(color: DefColor, alpha: int) -> Tuple[int, int, int, int]:
        """Blend a colour towards the panel background by its alpha."""
        factor = max(0, min(255, alpha)) / 255.0
        return (
            int(color[0] * factor),
            int(color[1] * factor),
            int(color[2] * factor),
        )

