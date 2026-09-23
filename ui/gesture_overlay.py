"""Gesture visualisation layer for the VisionCore camera viewport.

Renders the recognition result as part of the interface: a compact readout panel
and a restrained effect per gesture drawn on top of the existing landmark
overlay. The overlay only consumes data - it never performs recognition - and it
draws on cached alpha layers so the per frame cost stays flat.
"""

from __future__ import annotations

import math
import os
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.gestures import (
    Gesture,
    GesturePhase,
    GestureResult,
    GestureSnapshot,
    GestureState,
)
from app.hand_tracking import FINGER_JOINTS, WRIST, TrackingSnapshot
from ui.animations import PulseAnimation
from ui.hud import (
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BORDER,
    COLOR_STANDBY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)

Point = Tuple[float, float]

# Accents used for gesture effects: mint while held, amber while releasing
# (deliberately restrained, in keeping with the rest of the interface).
COLOR_GESTURE = COLOR_ONLINE
COLOR_GESTURE_SOFT = (150, 245, 205)
COLOR_RELEASE = COLOR_STANDBY
COLOR_RELEASE_SOFT = (255, 214, 140)

FADE_IN = 0.16
FADE_OUT = 0.24
PULSE_INTERVAL = 0.95
TRAIL_LENGTH = 14

# Finger chains highlighted per gesture (landmark index chains).
INDEX_CHAIN = FINGER_JOINTS["INDEX"]
MIDDLE_CHAIN = FINGER_JOINTS["MIDDLE"]
THUMB_CHAIN = FINGER_JOINTS["THUMB"]


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class GestureOverlay:
    """Draws gesture readouts and per gesture effects over the video."""

    def __init__(self) -> None:
        self._pulse = PulseAnimation(min_val=0.45, max_val=1.0, frequency_hz=1.4)
        self._fade = 0.0
        self._flash = 0.0
        self._pulse_timer = 0.0
        self._rings: List[float] = []
        self._trail: Deque[Point] = deque(maxlen=TRAIL_LENGTH)
        self._previous = Gesture.NONE
        self._layers: Dict[Tuple[str, int, int], pygame.Surface] = {}

    # -- animation --------------------------------------------------------- #

    def update(
        self,
        dt: float,
        gesture: GestureSnapshot,
        tracking: TrackingSnapshot,
    ) -> None:
        """Advance gesture animations from the current recognition snapshot."""
        self._pulse.update(dt)
        result = gesture.result
        active = result.recognized

        if active:
            self._fade = min(1.0, self._fade + dt / FADE_IN)
        else:
            self._fade = max(0.0, self._fade - dt / FADE_OUT)

        if result.gesture is not self._previous:
            self._flash = 1.0
            self._previous = result.gesture
            self._rings.clear()
            if result.gesture is Gesture.OPEN_PALM and active:
                self._rings.append(0.0)
        self._flash = max(0.0, self._flash - dt / 0.30)

        # Expanding radial pulses for the open palm.
        if active and result.gesture is Gesture.OPEN_PALM:
            self._pulse_timer += dt
            if self._pulse_timer >= PULSE_INTERVAL:
                self._pulse_timer = 0.0
                self._rings.append(0.0)
        else:
            self._pulse_timer = 0.0

        self._rings = [progress + dt / 0.75 for progress in self._rings if progress < 1.0]

        # Palm centre trail, used for the swipe motion trace.
        hand = tracking.primary
        if hand is not None:
            self._trail.append((hand.landmarks[WRIST].x, hand.landmarks[WRIST].y))
        elif not tracking.detected:
            self._trail.clear()

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        gesture: GestureSnapshot,
        tracking: TrackingSnapshot,
        fonts: Dict[str, pygame.font.Font],
        readout_anchor: Tuple[int, int],
    ) -> None:
        """Render the gesture layer inside the video viewport.

        ``readout_anchor`` is the bottom right corner the panel grows upwards
        from, which keeps it clear of the centred region where a hand is
        normally tracked.
        """
        result = gesture.result
        if result.recognized or result.phase is GesturePhase.RELEASE:
            hand = tracking.primary
            if hand is not None:
                self._draw_effect(surface, fitted, hand.landmarks, result)
        self._draw_readout(surface, gesture, fonts, readout_anchor)

    # -- effects ----------------------------------------------------------- #

    def _draw_effect(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        landmarks: Sequence,
        result: GestureResult,
    ) -> None:
        points = [
            (fitted.x + lm.x * fitted.width, fitted.y + lm.y * fitted.height)
            for lm in landmarks
        ]
        if len(points) < len(FINGER_JOINTS["PINKY"]):
            return

        releasing = result.phase is GesturePhase.RELEASE
        accent = COLOR_RELEASE if releasing else COLOR_GESTURE
        soft = COLOR_RELEASE_SOFT if releasing else COLOR_GESTURE_SOFT
        alpha_scale = self._fade if not releasing else max(0.35, self._fade)

        canvas = self._canvas_for(points, fitted)
        if canvas is None:
            return
        offset = canvas.topleft
        local = [(p[0] - offset[0], p[1] - offset[1]) for p in points]
        alpha, layer = self._layer("gesture", canvas.size, alpha_scale)

        gesture = result.gesture
        if gesture is Gesture.PINCH:
            self._effect_pinch(layer, local, alpha, accent, soft)
        elif gesture is Gesture.POINT:
            self._effect_point(layer, local, alpha, accent, soft)
        elif gesture is Gesture.TWO_FINGER:
            self._effect_two_finger(layer, local, alpha, accent, soft)
        elif gesture is Gesture.OPEN_PALM:
            self._effect_open_palm(layer, local, alpha, accent)
        elif gesture is Gesture.FIST:
            self._effect_fist(layer, local, alpha, accent, soft)
        elif gesture.is_motion:
            self._effect_swipe(surface, fitted, offset, alpha, accent)

        if gesture is not Gesture.NONE:
            self._highlight(layer, local, gesture, alpha, soft)
        surface.blit(layer, canvas.topleft)

    def _highlight(
        self,
        layer: pygame.Surface,
        local: Sequence[Point],
        gesture: Gesture,
        alpha: int,
        soft: Tuple[int, int, int],
    ) -> None:
        """Accent the landmark chains that produced the recognised gesture."""
        if gesture is Gesture.PINCH:
            chains = (THUMB_CHAIN, INDEX_CHAIN)
        elif gesture is Gesture.POINT:
            chains = (INDEX_CHAIN,)
        elif gesture is Gesture.TWO_FINGER:
            chains = (INDEX_CHAIN, MIDDLE_CHAIN)
        else:
            return

        for chain in chains:
            for a, b in zip(chain, chain[1:]):
                pygame.draw.line(layer, (*soft, alpha), local[a], local[b], 3)
            pygame.draw.circle(layer, (*soft, alpha), local[chain[-1]], 4, 1)

    def _effect_pinch(
        self,
        layer: pygame.Surface,
        local: Sequence[Point],
        alpha: int,
        accent: Tuple[int, int, int],
        soft: Tuple[int, int, int],
    ) -> None:
        """Circular lock indicator around the thumb/index contact point."""
        thumb_tip = local[THUMB_CHAIN[-1]]
        index_tip = local[INDEX_CHAIN[-1]]
        middle = ((thumb_tip[0] + index_tip[0]) / 2.0, (thumb_tip[1] + index_tip[1]) / 2.0)
        radius = 15
        pygame.draw.circle(layer, (*accent, alpha // 2), middle, radius, 1)
        pygame.draw.circle(
            layer,
            (*soft, int(alpha * self._pulse.value)),
            middle,
            int(radius * (0.55 + 0.25 * self._pulse.value)),
        )
        pygame.draw.line(layer, (*soft, alpha), thumb_tip, index_tip, 1)
        for tip in (thumb_tip, index_tip):
            pygame.draw.circle(layer, (*soft, alpha), tip, 2)

    def _effect_point(
        self,
        layer: pygame.Surface,
        local: Sequence[Point],
        alpha: int,
        accent: Tuple[int, int, int],
        soft: Tuple[int, int, int],
    ) -> None:
        """Directional guide through the extended index finger."""
        wrist = local[WRIST]
        tip = local[INDEX_CHAIN[-1]]
        vx, vy = tip[0] - wrist[0], tip[1] - wrist[1]
        length = math.hypot(vx, vy)
        if length < 1.0:
            return
        ux, uy = vx / length, vy / length
        end = (tip[0] + ux * 34, tip[1] + uy * 34)
        pygame.draw.line(layer, (*accent, alpha // 2), tip, end, 1)
        for distance, size in ((16, 6), (30, 9)):
            cx, cy = tip[0] + ux * distance, tip[1] + uy * distance
            px, py = -uy, ux
            pygame.draw.lines(
                layer,
                (*soft, alpha),
                False,
                [
                    (cx - ux * size + px * size, cy - uy * size + py * size),
                    (cx, cy),
                    (cx - ux * size - px * size, cy - uy * size - py * size),
                ],
                1,
            )

    def _effect_two_finger(
        self,
        layer: pygame.Surface,
        local: Sequence[Point],
        alpha: int,
        accent: Tuple[int, int, int],
        soft: Tuple[int, int, int],
    ) -> None:
        """Dual guide lines along the two extended fingers."""
        for chain in (INDEX_CHAIN, MIDDLE_CHAIN):
            base = local[chain[0]]
            tip = local[chain[-1]]
            vx, vy = tip[0] - base[0], tip[1] - base[1]
            length = math.hypot(vx, vy)
            if length < 1.0:
                continue
            ux, uy = vx / length, vy / length
            start = (base[0] - ux * 6, base[1] - uy * 6)
            end = (tip[0] + ux * 22, tip[1] + uy * 22)
            pygame.draw.line(layer, (*accent, alpha // 2), start, end, 1)
            px, py = -uy * 5, ux * 5
            pygame.draw.line(
                layer,
                (*soft, alpha),
                (end[0] + px, end[1] + py),
                (end[0] - px, end[1] - py),
                2,
            )

    def _effect_open_palm(
        self,
        layer: pygame.Surface,
        local: Sequence[Point],
        alpha: int,
        accent: Tuple[int, int, int],
    ) -> None:
        """Soft expanding radial pulse from the palm centre."""
        centre = local[FINGER_JOINTS["MIDDLE"][0]]
        spread = max(28.0, _span(local) * 0.75)
        for progress in self._rings:
            radius = int(spread * (0.35 + 0.85 * _ease_out_cubic(progress)))
            fade = int(alpha * 0.55 * (1.0 - progress))
            if fade > 4:
                pygame.draw.circle(
                    layer, (*accent, fade), (int(centre[0]), int(centre[1])), radius, 1
                )
        # A faint inner ring keeps the palm reading as "active" without a solid
        # fill that would obscure the hand.
        glow = int(alpha * 0.42 * self._pulse.value)
        if glow > 4:
            pygame.draw.circle(
                layer,
                (*accent, glow),
                (int(centre[0]), int(centre[1])),
                int(spread * (0.30 + 0.06 * self._pulse.value)),
                1,
            )

    def _effect_fist(
        self,
        layer: pygame.Surface,
        local: Sequence[Point],
        alpha: int,
        accent: Tuple[int, int, int],
        soft: Tuple[int, int, int],
    ) -> None:
        """Compact lock bracket that snaps onto the closed hand."""
        progress = _ease_out_cubic(self._fade)
        pad = int(12 - 4 * progress)
        rect = _bounds(local, pad)
        arm = 9
        inset = int((1.0 - progress) * 6)
        outer = rect.inflate(inset * 2, inset * 2)
        pygame.draw.rect(layer, (*accent, alpha // 3), rect, 1)
        for x, y, dx, dy in (
            (outer.left, outer.top, 1, 1),
            (outer.right, outer.top, -1, 1),
            (outer.left, outer.bottom, 1, -1),
            (outer.right, outer.bottom, -1, -1),
        ):
            pygame.draw.line(layer, (*soft, alpha), (x, y), (x + arm * dx, y), 2)
            pygame.draw.line(layer, (*soft, alpha), (x, y), (x, y + arm * dy), 2)

    def _effect_swipe(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        offset: Tuple[int, int],
        alpha: int,
        accent: Tuple[int, int, int],
    ) -> None:
        """Fading motion trail in the direction of travel."""
        if len(self._trail) < 3:
            return
        trail = [
            (
                int(fitted.x + x * fitted.width) - offset[0],
                int(fitted.y + y * fitted.height) - offset[1],
            )
            for x, y in self._trail
        ]
        count = len(trail)
        for index in range(1, count):
            fade = int(alpha * 0.75 * (index / count) ** 1.6)
            if fade > 5:
                pygame.draw.line(
                    surface,
                    (*accent, fade),
                    trail[index - 1],
                    trail[index],
                    2,
                )
        head = trail[-1]
        previous = trail[-2]
        vx, vy = head[0] - previous[0], head[1] - previous[1]
        length = math.hypot(vx, vy)
        if length < 1.0:
            return
        ux, uy = vx / length, vy / length
        for step, size in ((12, 7), (26, 10)):
            cx, cy = head[0] + ux * step, head[1] + uy * step
            px, py = -uy * size, ux * size
            pygame.draw.lines(
                surface,
                (*accent, int(alpha * 0.8)),
                False,
                [
                    (cx - ux * size + px, cy - uy * size + py),
                    (cx, cy),
                    (cx - ux * size - px, cy - uy * size - py),
                ],
                1,
            )

    # -- readout panel ----------------------------------------------------- #

    def _draw_readout(
        self,
        surface: pygame.Surface,
        gesture: GestureSnapshot,
        fonts: Dict[str, pygame.font.Font],
        anchor: Tuple[int, int],
    ) -> None:
        """Compact gesture readout pinned to the viewport's lower right corner."""
        result = gesture.result
        releasing = result.phase is GesturePhase.RELEASE
        recognised = result.recognized or releasing

        accent = COLOR_RELEASE if releasing else (COLOR_GESTURE if recognised else COLOR_ICE_BLUE)
        headline = "GESTURE"
        rows: List[Tuple[str, str]] = []
        if gesture.state is GestureState.DISABLED:
            value = "OFFLINE"
            accent = COLOR_TEXT_MUTED
        else:
            value = result.gesture.label if recognised else "SEARCHING"
            if recognised:
                rows.append(("CONFIDENCE", f"{result.confidence * 100:.1f}%"))
                rows.append(("STATE", "RELEASE" if releasing else "ACTIVE"))
                if result.handedness:
                    rows.append(("HAND", result.handedness))

        padding = 12
        width = 216
        row_height = 16
        height = padding * 2 + 34 + row_height * len(rows)

        _, layer = self._layer("readout", (width, height), 1.0)
        layer.fill((*(7, 14, 23), 200))
        pygame.draw.rect(layer, (*COLOR_PANEL_BORDER, 225), layer.get_rect(), 1)
        pygame.draw.line(layer, (*accent, 215), (0, 0), (0, height), 2)

        title = fonts["mono_small"].render(headline, True, COLOR_TEXT_MUTED)
        layer.blit(title, (padding, padding - 2))
        pygame.draw.line(
            layer,
            (*COLOR_PANEL_BORDER, 200),
            (padding, padding + 14),
            (width - padding, padding + 14),
            1,
        )

        value_surface = fonts["subheading"].render(value, True, accent)
        layer.blit(value_surface, (padding, padding + 16))

        if gesture.state is GestureState.RECOGNIZED:
            dot_alpha = int(160 + 90 * self._pulse.value)
            pygame.draw.circle(
                layer,
                (*accent, dot_alpha),
                (width - padding - 6, padding + 24),
                4,
            )

        flash = self._flash
        if flash > 0.0:
            sweep_alpha = int(120 * flash)
            sweep_width = int(10 + 50 * (1.0 - flash))
            _, sweep_layer = self._layer("readout_flash", (sweep_width, height), 1.0)
            for column in range(sweep_width):
                column_alpha = int(sweep_alpha * (1.0 - column / sweep_width))
                pygame.draw.line(
                    sweep_layer, (*accent, column_alpha), (column, 0), (column, height)
                )
            layer.blit(sweep_layer, (int((width - sweep_width) * (1.0 - flash)), 0))

        offset = padding + 34
        for label, text in rows:
            label_surface = fonts["mono_small"].render(label, True, COLOR_TEXT_MUTED)
            layer.blit(label_surface, (padding, offset))
            value_surface = fonts["mono_small"].render(text, True, COLOR_TEXT_WHITE)
            layer.blit(value_surface, (width - padding - value_surface.get_width(), offset))
            offset += row_height

        surface.blit(layer, (anchor[0] - width, anchor[1] - height))

    # -- helpers ----------------------------------------------------------- #

    def _canvas_for(self, points: Sequence[Point], fitted: pygame.Rect) -> Optional[pygame.Rect]:
        margin = 56
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        rect = pygame.Rect(
            int(min(xs)) - margin,
            int(min(ys)) - margin,
            int(max(xs) - min(xs)) + margin * 2,
            int(max(ys) - min(ys)) + margin * 2,
        ).clip(fitted)
        if rect.width < 8 or rect.height < 8:
            return None
        return rect

    def _layer(
        self,
        kind: str,
        size: Tuple[int, int],
        alpha_scale: float,
    ) -> Tuple[int, pygame.Surface]:
        """Return a cleared cached layer plus the alpha level for this frame."""
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        key = (kind, width, height)
        layer = self._layers.get(key)
        if layer is None:
            if len(self._layers) > 12:
                self._layers.clear()
            layer = pygame.Surface((width, height), pygame.SRCALPHA)
            self._layers[key] = layer
        else:
            layer.fill((0, 0, 0, 0))
        return int(210 * max(0.0, min(1.0, alpha_scale))), layer


def _span(points: Sequence[Point]) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _bounds(points: Sequence[Point], pad: int) -> pygame.Rect:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return pygame.Rect(
        int(min(xs)) - pad,
        int(min(ys)) - pad,
        int(max(xs) - min(xs)) + pad * 2,
        int(max(ys) - min(ys)) + pad * 2,
    )
