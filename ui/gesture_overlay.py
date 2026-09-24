"""Gesture cue layer for the VisionCore camera viewport.

This layer ties a recognised gesture to what the control layers actually did:
a pulse at the pinch point right after a real click, direction arrows while a
real scroll or device action fires, and safety colouring while an emergency
stop is engaged. The recognised gesture's finger chains are traced softly so
the gesture label in the viewport can be matched to the hand.

It consumes recognition results only - it never performs recognition - and it
draws on cached alpha layers so the per frame cost stays flat.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.controls.safety import ControlAction, ControlMode, ControlState
from app.gestures import Gesture, GesturePhase, GestureSnapshot
from app.hand_tracking import FINGER_JOINTS, TrackingSnapshot
from app.state import Telemetry
from ui.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_SUCCESS

Point = Tuple[float, float]

INDEX_CHAIN = FINGER_JOINTS["INDEX"]
MIDDLE_CHAIN = FINGER_JOINTS["MIDDLE"]
THUMB_CHAIN = FINGER_JOINTS["THUMB"]


class GestureOverlay:
    """Draws action cues over the video for gestures that really acted."""

    def __init__(self) -> None:
        self._layers: Dict[Tuple[str, int, int], pygame.Surface] = {}

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        fitted_rect: pygame.Rect,
        gesture: GestureSnapshot,
        tracking: TrackingSnapshot,
        telemetry: Telemetry,
    ) -> None:
        """Render the cue layer inside the video viewport."""
        result = gesture.result
        active = result.recognized or result.phase is GesturePhase.RELEASE
        hand = tracking.primary if active else None
        if hand is None or len(hand.landmarks) < len(FINGER_JOINTS["PINKY"]):
            return

        points = [
            (fitted_rect.x + lm.x * fitted_rect.width,
             fitted_rect.y + lm.y * fitted_rect.height)
            for lm in hand.landmarks
        ]
        canvas = self._canvas_for(points, fitted_rect)
        if canvas is None:
            return

        offset = canvas.topleft
        local = [(p[0] - offset[0], p[1] - offset[1]) for p in points]
        layer = self._layer("cues", canvas.size)

        self._trace_chains(layer, local, result.gesture)
        self._cue_click_pulse(layer, local, telemetry)
        self._cue_direction(layer, local, telemetry)
        self._cue_safety(layer, local, telemetry)

        surface.blit(layer, canvas.topleft)

    # -- cues -------------------------------------------------------------- #

    @staticmethod
    def _trace_chains(
        layer: pygame.Surface,
        local: Sequence[Point],
        gesture: Gesture,
    ) -> None:
        """Softly trace the finger chains that produced the gesture."""
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
                pygame.draw.line(
                    layer, (*COLOR_ACCENT, 120), local[a], local[b], 3
                )
            pygame.draw.circle(layer, (*COLOR_ACCENT, 150), local[chain[-1]], 4, 1)

    @staticmethod
    def _cue_click_pulse(
        layer: pygame.Surface,
        local: Sequence[Point],
        telemetry: Telemetry,
    ) -> None:
        """Expanding pulse at the pinch point right after a real click."""
        action = telemetry.control_action
        age = telemetry.control_action_age
        if action not in (ControlAction.LEFT_CLICK, ControlAction.DRAG_START):
            return
        if age > 0.45:
            return
        progress = min(1.0, max(0.0, age / 0.45))
        thumb_tip = local[THUMB_CHAIN[-1]]
        index_tip = local[INDEX_CHAIN[-1]]
        centre = (
            int((thumb_tip[0] + index_tip[0]) / 2),
            int((thumb_tip[1] + index_tip[1]) / 2),
        )
        fade = int(210 * (1.0 - progress))
        if fade <= 4:
            return
        pygame.draw.circle(layer, (*COLOR_SUCCESS, fade), centre, int(10 + 20 * progress), 1)
        pygame.draw.circle(layer, (*COLOR_SUCCESS, min(230, fade * 2)), centre, 3)

    @staticmethod
    def _cue_direction(
        layer: pygame.Surface,
        local: Sequence[Point],
        telemetry: Telemetry,
    ) -> None:
        """Direction arrows for a real scroll or device action that fired."""
        up, color = GestureOverlay._direction(telemetry)
        if up is None:
            return
        index_tip = local[INDEX_CHAIN[-1]]
        middle_tip = local[MIDDLE_CHAIN[-1]]
        centre_x = int((index_tip[0] + middle_tip[0]) / 2)
        centre_y = int((index_tip[1] + middle_tip[1]) / 2) + 24
        direction = -1 if up else 1
        for step, size in ((5, 5), (12, 8)):
            cy = centre_y + direction * step
            pygame.draw.lines(
                layer, (*color, 200), False,
                [(centre_x - size, cy - direction * size), (centre_x, cy),
                 (centre_x + size, cy - direction * size)],
                1,
            )

    @staticmethod
    def _direction(telemetry: Telemetry):
        """``(up, colour)`` for a real scroll or device action, else None."""
        if telemetry.control_mode is ControlMode.MOUSE:
            action = telemetry.control_action
            if telemetry.control_action_age > 0.6:
                return None
            if action is ControlAction.SCROLL_UP:
                return True, COLOR_SUCCESS
            if action is ControlAction.SCROLL_DOWN:
                return False, COLOR_SUCCESS
            return None

        action = telemetry.device_action
        if telemetry.device_action_age > 0.8 or action is None:
            return None
        name = getattr(action, "value", "")
        if name == "VOLUME_UP":
            return True, COLOR_ACCENT
        if name == "VOLUME_DOWN":
            return False, COLOR_ACCENT
        if name == "BRIGHTNESS_UP":
            return True, COLOR_ACCENT
        if name == "BRIGHTNESS_DOWN":
            return False, COLOR_ACCENT
        return None

    @staticmethod
    def _cue_safety(
        layer: pygame.Surface,
        local: Sequence[Point],
        telemetry: Telemetry,
    ) -> None:
        """A red halo on the palm while an emergency stop is engaged."""
        emergency = (
            telemetry.control_state is ControlState.EMERGENCY_STOP
            or telemetry.device_state is ControlState.EMERGENCY_STOP
        )
        if not emergency:
            return
        centre = local[FINGER_JOINTS["MIDDLE"][0]]
        radius = 34
        pygame.draw.circle(
            layer, (*COLOR_DANGER, 170),
            (int(centre[0]), int(centre[1])), radius, 2,
        )
        pygame.draw.circle(
            layer, (*COLOR_DANGER, 80),
            (int(centre[0]), int(centre[1])), radius + 6, 1,
        )

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _canvas_for(points: Sequence[Point], fitted: pygame.Rect) -> Optional[pygame.Rect]:
        margin = 48
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

    def _layer(self, kind: str, size: Tuple[int, int]) -> pygame.Surface:
        """Return a cleared cached alpha layer."""
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        key = (kind, width, height)
        layer = self._layers.get(key)
        if layer is None:
            if len(self._layers) > 8:
                self._layers.clear()
            layer = pygame.Surface((width, height), pygame.SRCALPHA)
            self._layers[key] = layer
        else:
            layer.fill((0, 0, 0, 0))
        return layer
