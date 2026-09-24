"""Camera viewport: rounded video surface, letterbox scaling and clean overlays.

The camera feed is the product, so the viewport is a large rounded well with a
hairline border and no chrome on the video itself. Overlays are limited to what
the user actually needs while using the application:

* the hand skeleton and the gesture cues (real tracking output);
* one small floating information line with the measured resolution, frame rate
  and tracking state;
* a gesture pill while a gesture is recognised;
* the optional diagnostics pill (toggled with ``P``) with measured latencies;
* the notification and error strip, which report real action results.

Frame conversion is unchanged from the validated pipeline: downscaling is
delegated to OpenCV (INTER_AREA) and native-size frames are handed to pygame
with zero copies. Nothing is created per frame except small pill surfaces.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.gestures import Gesture, GesturePhase, GestureSnapshot
from app.hand_tracking import TrackingSnapshot, TrackingState
from app.interaction import RecoveryAction
from app.state import SubsystemState, Telemetry
from ui.feedback_view import FeedbackView
from ui.gesture_overlay import GestureOverlay
from ui.hand_overlay import HandOverlay
from ui.theme import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_TEXT_DIM,
    COLOR_TEXT,
    COLOR_WELL,
    glass_pill,
    round_video,
)

VIEWPORT_RADIUS = 14


class CameraView:
    """Renders the video viewport with aspect-ratio preservation and overlays."""

    def __init__(self) -> None:
        self.hand_overlay = HandOverlay()
        self.gesture_overlay = GestureOverlay()
        self.feedback_view = FeedbackView()

    def update(
        self,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> None:
        """Advance animation states."""
        self.hand_overlay.update(dt, tracking)

    # -- geometry ---------------------------------------------------------- #

    @staticmethod
    def _calculate_letterbox(
        frame_w: int,
        frame_h: int,
        container_rect: pygame.Rect,
    ) -> pygame.Rect:
        """Compute fitted rectangle maintaining aspect ratio within container."""
        if frame_w <= 0 or frame_h <= 0:
            return container_rect

        frame_aspect = frame_w / frame_h
        container_aspect = container_rect.width / container_rect.height

        if container_aspect > frame_aspect:
            # Container is wider: pillarbox (bars on left/right)
            scaled_h = container_rect.height
            scaled_w = int(scaled_h * frame_aspect)
            scaled_x = container_rect.x + (container_rect.width - scaled_w) // 2
            scaled_y = container_rect.y
        else:
            # Container is taller: letterbox (bars on top/bottom)
            scaled_w = container_rect.width
            scaled_h = int(scaled_w / frame_aspect)
            scaled_x = container_rect.x
            scaled_y = container_rect.y + (container_rect.height - scaled_h) // 2

        return pygame.Rect(scaled_x, scaled_y, scaled_w, scaled_h)

    # -- surfaces ---------------------------------------------------------- #

    @staticmethod
    def _frame_to_surface(frame: np.ndarray, target_size: Tuple[int, int]) -> pygame.Surface:
        """Convert an RGB frame into a Pygame surface scaled to the viewport.

        Downscaling is delegated to OpenCV (INTER_AREA), which is cheaper and
        produces fewer artefacts than a bilinear upscale-then-rotate style scaler.
        """
        source_height, source_width = frame.shape[:2]
        target_width, target_height = target_size

        if (source_width, source_height) == (target_width, target_height):
            return pygame.image.frombuffer(frame.tobytes(), target_size, "RGB")

        if target_width < source_width and target_height < source_height:
            scaled = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
            return pygame.image.frombuffer(scaled.tobytes(), target_size, "RGB")

        surface = pygame.image.frombuffer(frame.tobytes(), (source_width, source_height), "RGB")
        return pygame.transform.smoothscale(surface, target_size)

    # -- pills ------------------------------------------------------------- #

    def _draw_pill(
        self,
        surface: pygame.Surface,
        text: str,
        font: pygame.font.Font,
        color: Tuple[int, int, int],
        anchor: Tuple[int, int],
        align_right: bool = False,
        dot_color: Optional[Tuple[int, int, int]] = None,
    ) -> pygame.Rect:
        """Draw a compact translucent pill with an optional status dot."""
        label = font.render(text, True, color)
        padding = 10
        dot_space = 12 if dot_color else 0
        width = label.get_width() + padding * 2 + dot_space
        height = label.get_height() + 10

        x = anchor[0] - width if align_right else anchor[0]
        rect = pygame.Rect(x, anchor[1], width, height)

        pill = glass_pill((width, height))
        if dot_color:
            pygame.draw.circle(pill, dot_color, (padding + 3, height // 2), 3)
        pill.blit(label, (padding + dot_space, 5))
        surface.blit(pill, rect.topleft)
        return rect

    # -- main render ------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        viewport_rect: pygame.Rect,
        frame: Optional[np.ndarray],
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> Optional[Tuple[RecoveryAction, pygame.Rect]]:
        """Render the viewport: well, video, tracking layers and overlays.

        Returns the rectangle of the RETRY button when an error state with a
        real recovery is shown, so the page can route the click.
        """
        # The well: a rounded, near-black surface that frames the video.
        pygame.draw.rect(surface, COLOR_WELL, viewport_rect, border_radius=VIEWPORT_RADIUS)
        pygame.draw.rect(surface, COLOR_BORDER, viewport_rect, 1, border_radius=VIEWPORT_RADIUS)

        fitted_rect = viewport_rect.copy()

        if frame is not None and frame.size > 0:
            frame_height, frame_width = frame.shape[:2]
            fitted_rect = self._calculate_letterbox(frame_width, frame_height, viewport_rect)

            frame_surf = self._frame_to_surface(
                frame, (fitted_rect.width, fitted_rect.height)
            )
            surface.blit(frame_surf, fitted_rect.topleft)

            # Rounded corners on the video itself (cached corner patches).
            if fitted_rect == viewport_rect:
                round_video(surface, fitted_rect, VIEWPORT_RADIUS, COLOR_WELL)
                pygame.draw.rect(
                    surface, COLOR_BORDER, viewport_rect, 1, border_radius=VIEWPORT_RADIUS
                )

            # Tracking layers, clipped to the video area so nothing can spill
            # into the letterbox bars or outside the viewport.
            previous_clip = surface.get_clip()
            surface.set_clip(fitted_rect)
            try:
                self.hand_overlay.render(surface, fitted_rect, tracking)
                self.gesture_overlay.render(
                    surface, fitted_rect, gesture, tracking, telemetry
                )
            finally:
                surface.set_clip(previous_clip)

            self._draw_viewport_pills(surface, fitted_rect, telemetry, fonts, gesture)

        # Feedback last: a notification reports a real result and an error strip
        # offers a recovery, so neither may be hidden behind another layer.
        return self.feedback_view.render_overlay(surface, viewport_rect, telemetry, fonts)

    def _draw_viewport_pills(
        self,
        surface: pygame.Surface,
        fitted_rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        gesture: GestureSnapshot,
    ) -> None:
        """Gesture pill, information line and optional diagnostics."""
        font = fonts["small"]

        # Gesture pill, top-left: shown only while a gesture is recognised, so
        # the resting viewport stays quiet.
        result = gesture.result
        releasing = result.phase is GesturePhase.RELEASE
        if result.recognized or releasing:
            name = result.gesture.label if result.gesture is not Gesture.NONE else "None"
            confidence = (
                f"{result.confidence * 100:.0f}%"
                if result.recognized and result.confidence
                else ""
            )
            label = f"{name} · {confidence}" if confidence else name
            if releasing:
                label = f"{label} · releasing"
            self._draw_pill(
                surface, label, font, COLOR_TEXT,
                (fitted_rect.x + 14, fitted_rect.y + 14),
                dot_color=COLOR_ACCENT,
            )

        # Information line, bottom-left: measured resolution, frame rate and
        # the real tracking state.
        resolution = (
            f"{telemetry.camera_width} × {telemetry.camera_height}"
            if telemetry.camera_width > 0
            else "—"
        )
        fps = telemetry.camera_fps if telemetry.camera_fps > 0 else telemetry.render_fps
        fps_text = f"{fps:.1f} FPS" if fps > 0 else "— FPS"
        info = f"{resolution} · {fps_text} · {self._tracking_text(telemetry)}"
        self._draw_pill(
            surface, info, font, COLOR_TEXT_DIM,
            (fitted_rect.x + 14, fitted_rect.bottom - 14 - (font.get_height() + 10)),
        )

        # Diagnostics pill, bottom-right: measured values only, and only while
        # the user has the diagnostics readout switched on ([P]).
        if telemetry.diagnostics_visible:
            self._draw_pill(
                surface,
                self._performance_text(telemetry),
                fonts["mono_small"],
                COLOR_TEXT_DIM,
                (fitted_rect.right - 14, fitted_rect.bottom - 14 - (font.get_height() + 10)),
                align_right=True,
            )

    @staticmethod
    def _tracking_text(telemetry: Telemetry) -> str:
        """One or two words describing the real tracking state."""
        if telemetry.tracking is SubsystemState.DISABLED:
            return "Tracking off"
        if telemetry.tracking in (SubsystemState.UNAVAILABLE, SubsystemState.ERROR):
            return "Tracking unavailable"
        state = telemetry.tracking_state
        if state is TrackingState.TRACKING:
            return "Tracking locked"
        if state is TrackingState.DETECTING:
            return "Acquiring hand"
        if state is TrackingState.HAND_LOST:
            return "Hand lost"
        return "Searching"

    @staticmethod
    def _performance_text(telemetry: Telemetry) -> str:
        """FPS and subsystem costs, with ``--`` when unmeasured.

        Every value is measured by the subsystem that owns it; a metric that has
        not been produced yet is shown as ``--`` rather than estimated.
        """
        def value(number: float, decimals: int, suffix: str) -> str:
            return f"{number:.{decimals}f} {suffix}" if number > 0 else "--"

        fps = f"{telemetry.render_fps:.0f}" if telemetry.render_fps > 0 else "--"
        return (
            f"FPS {fps} · TRACK {value(telemetry.tracking_latency_ms, 1, 'ms')} · "
            f"GESTURE {value(telemetry.gesture_latency_ms, 2, 'ms')} · "
            f"CONTROL {value(telemetry.control_latency_ms, 2, 'ms')}"
        )
