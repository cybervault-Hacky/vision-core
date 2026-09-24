"""Hand tracking visualisation for the VisionCore camera viewport.

The tracked hand is drawn as a clean anatomical skeleton: thin bright bones,
quiet joints, slightly emphasised fingertips. Everything decorative - brackets,
scan beams, status blocks, bloom passes - is deliberately absent: the camera
feed is the product, and the skeleton is the one overlay that must stay legible
on top of it.

A short pose trace survives the tracker's hold window and fades once the hand is
lost, so tracking transitions read as continuous rather than snapping. It is the
only alpha layer in this module; the skeleton itself draws opaque lines directly
onto the viewport, which keeps the per frame cost a few dozen draw calls with no
allocations.
"""

from __future__ import annotations

import os
from typing import Dict, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.hand_tracking import (
    HAND_CONNECTIONS,
    PALM_CONNECTIONS,
    WRIST,
    Hand,
    TrackingSnapshot,
)
from ui.theme import COLOR_ACCENT, COLOR_HAND_SECONDARY, COLOR_WARNING

# Animation timings (seconds).
GHOST_FADE_DURATION = 0.5

# Fingertip landmarks get a slightly stronger marker than the phalanges.
FINGERTIPS = (4, 8, 12, 16, 20)

Point = Tuple[float, float]


class HandOverlay:
    """Draws tracked hand skeletons and the brief loss trace."""

    def __init__(self) -> None:
        self._ghost: Tuple = ()
        self._ghost_alpha = 0.0
        self._layers: Dict[Tuple[str, int, int], pygame.Surface] = {}

    # -- animation --------------------------------------------------------- #

    def update(self, dt: float, snapshot: TrackingSnapshot) -> None:
        """Advance the loss trace from the current tracking snapshot."""
        state = snapshot.state
        if state.is_engaged or snapshot.detected:
            self._ghost_alpha = 1.0
        elif self._ghost:
            self._ghost_alpha = max(0.0, self._ghost_alpha - dt / GHOST_FADE_DURATION)
            if self._ghost_alpha == 0.0:
                self._ghost = ()

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        fitted_rect: pygame.Rect,
        snapshot: TrackingSnapshot,
    ) -> None:
        """Render the tracking layer inside the video viewport."""
        hands = snapshot.hands

        if snapshot.detected and hands:
            self._ghost = hands[0].landmarks
            for index, hand in enumerate(hands):
                self._draw_hand(surface, fitted_rect, hand, index)
        elif self._ghost and self._ghost_alpha > 0.0:
            self._draw_ghost(surface, fitted_rect, self._ghost_alpha)

    # -- skeleton ---------------------------------------------------------- #

    def _draw_hand(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        hand: Hand,
        index: int,
    ) -> None:
        """Render one hand skeleton with quiet, consistent weighting."""
        points = self._to_pixels(hand.landmarks, fitted)
        if len(points) < 2:
            return

        accent = COLOR_ACCENT if index == 0 else COLOR_HAND_SECONDARY
        connections = [
            (a, b) for a, b in HAND_CONNECTIONS if a < len(points) and b < len(points)
        ]

        # Bones first, palm bones slightly stronger so the hand reads as a
        # structure rather than a wire mesh.
        for a, b in connections:
            is_palm = (a, b) in PALM_CONNECTIONS
            pygame.draw.line(
                surface, accent, points[a], points[b], 2 if is_palm else 1
            )

        # Joints: small quiet nodes.
        for point in points:
            pygame.draw.circle(surface, accent, (int(point[0]), int(point[1])), 2)

        # Fingertips and wrist carry the emphasis.
        for i in FINGERTIPS:
            if i < len(points):
                pygame.draw.circle(
                    surface, accent, (int(points[i][0]), int(points[i][1])), 3
                )
        if len(points) > WRIST:
            wrist = points[WRIST]
            pygame.draw.circle(
                surface, accent, (int(wrist[0]), int(wrist[1])), 3
            )
            pygame.draw.circle(
                surface, accent, (int(wrist[0]), int(wrist[1])), 6, 1
            )

    def _draw_ghost(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        alpha: float,
    ) -> None:
        """Brief amber trace of the last known pose while tracking recovers."""
        points = self._to_pixels(self._ghost, fitted)
        if len(points) < 2:
            return

        bounds = self._bounds(points, margin=16, clip=fitted)
        if bounds is None:
            return

        offset = bounds.topleft
        local = [(px - offset[0], py - offset[1]) for px, py in points]
        layer = self._layer("ghost", bounds.size)

        line_alpha = int(120 * alpha)
        node_alpha = int(140 * alpha)
        for a, b in HAND_CONNECTIONS:
            if a < len(local) and b < len(local):
                pygame.draw.line(
                    layer, (*COLOR_WARNING, line_alpha), local[a], local[b], 1
                )
        for point in local:
            pygame.draw.circle(layer, (*COLOR_WARNING, node_alpha),
                               (int(point[0]), int(point[1])), 2)

        surface.blit(layer, bounds.topleft)

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _to_pixels(landmarks: Sequence, fitted: pygame.Rect) -> list:
        """Map normalised (mirrored) landmarks onto the displayed video rect."""
        width = fitted.width
        height = fitted.height
        return [
            (fitted.x + lm.x * width, fitted.y + lm.y * height)
            for lm in landmarks
        ]

    @staticmethod
    def _bounds(points: Sequence[Point], margin: int, clip: pygame.Rect):
        """Bounding rect around a point cloud, padded and clipped."""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        rect = pygame.Rect(
            int(min(xs)) - margin,
            int(min(ys)) - margin,
            int(max(xs) - min(xs)) + margin * 2,
            int(max(ys) - min(ys)) + margin * 2,
        )
        clipped = rect.clip(clip)
        return clipped if clipped.width > 4 and clipped.height > 4 else None

    def _layer(self, kind: str, size: Tuple[int, int]) -> pygame.Surface:
        """Return a cleared, cached alpha layer of the requested kind and size."""
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
