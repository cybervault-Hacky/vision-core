"""Camera viewport renderer, letterbox scaling, and futuristic scanning HUD."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, Optional, Tuple

import cv2
import numpy as np

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.gestures import GestureSnapshot
from app.hand_tracking import TrackingSnapshot
from app.interaction import RecoveryAction
from app.state import Telemetry
from ui.animations import PulseAnimation
from ui.feedback_view import FeedbackView
from ui.focus_view import FocusView
from ui.gesture_overlay import GestureOverlay
from ui.hand_overlay import HandOverlay
from ui.hud import (
    COLOR_CYAN_PRIMARY,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)

if TYPE_CHECKING:  # the HUD manager is only used to type the injected helper
    from ui.hud import HUDManager


class CameraView:
    """Renders the video viewport with aspect-ratio preservation and HUD overlays."""

    def __init__(self, hud: Optional["HUDManager"] = None) -> None:
        self.pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.0)
        self.hand_overlay = HandOverlay()
        self.gesture_overlay = GestureOverlay()
        self.focus_view = FocusView()
        self.feedback_view = FeedbackView(hud=hud)

    def update(
        self,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> None:
        """Advance animation states."""
        self.pulse.update(dt)
        self.hand_overlay.update(dt, tracking)
        self.gesture_overlay.update(dt, gesture, tracking)

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

    # -- static chrome ----------------------------------------------------- #

    @staticmethod
    def draw_corner_brackets(
        surface: pygame.Surface,
        rect: pygame.Rect,
        bracket_len: int = 24,
        thickness: int = 2,
    ) -> None:
        """Render futuristic geometric corner brackets around a viewport."""
        b_len = min(bracket_len, rect.width // 4, rect.height // 4)
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        color = COLOR_CYAN_PRIMARY

        pygame.draw.line(surface, color, (x, y), (x + b_len, y), thickness)
        pygame.draw.line(surface, color, (x, y), (x, y + b_len), thickness)
        pygame.draw.line(surface, color, (x + w, y), (x + w - b_len, y), thickness)
        pygame.draw.line(surface, color, (x + w, y), (x + w, y + b_len), thickness)
        pygame.draw.line(surface, color, (x, y + h), (x + b_len, y + h), thickness)
        pygame.draw.line(surface, color, (x, y + h), (x, y + h - b_len), thickness)
        pygame.draw.line(surface, color, (x + w, y + h), (x + w - b_len, y + h), thickness)
        pygame.draw.line(surface, color, (x + w, y + h), (x + w, y + h - b_len), thickness)

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

    def _overlay_badge(
        self,
        surface: pygame.Surface,
        text: str,
        font: pygame.font.Font,
        color: Tuple[int, int, int],
        anchor: Tuple[int, int],
        align_right: bool = False,
        dot_color: Optional[Tuple[int, int, int]] = None,
    ) -> pygame.Rect:
        """Draw a compact translucent telemetry badge."""
        label = font.render(text, True, color)
        padding = 8
        dot_space = 14 if dot_color else 0
        width = label.get_width() + padding * 2 + dot_space
        height = label.get_height() + 8

        x = anchor[0] - width if align_right else anchor[0]
        rect = pygame.Rect(x, anchor[1], width, height)

        badge = pygame.Surface((width, height), pygame.SRCALPHA)
        badge.fill((9, 17, 27, 205))
        pygame.draw.rect(badge, (28, 56, 84, 220), badge.get_rect(), 1)
        if dot_color:
            pygame.draw.circle(badge, dot_color, (padding + 3, height // 2), 4)
        badge.blit(label, (padding + dot_space, 4))
        surface.blit(badge, rect.topleft)
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
        """Render the complete camera viewport, video surface, and HUD layers.

        Draw order is deliberate: video, focus ring (so the hand is never
        covered by chrome), hand overlay, badges, gesture effects, and finally
        the action feedback layer, which must stay readable above everything
        else. Returns the rectangle of the RETRY button when an error state with
        a real recovery is shown, so the window can route the click.
        """
        pygame.draw.rect(surface, (5, 8, 12), viewport_rect)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, viewport_rect, 1)

        fitted_rect = viewport_rect

        if frame is not None and frame.size > 0:
            frame_height, frame_width = frame.shape[:2]
            fitted_rect = self._calculate_letterbox(frame_width, frame_height, viewport_rect)

            frame_surf = self._frame_to_surface(
                frame, (fitted_rect.width, fitted_rect.height)
            )
            surface.blit(frame_surf, fitted_rect.topleft)

            # Central focus: status ring, focus readout and tracking quality. It
            # is drawn before the hand so tracked geometry stays legible.
            self.focus_view.render(surface, fitted_rect, telemetry, fonts)

            # Hand tracking layer reacts to the real tracking state. It is
            # clipped to the video area so no overlay can ever spill into the
            # letterbox bars, the header or the sidebar.
            surface.set_clip(fitted_rect)
            try:
                self.hand_overlay.render(surface, fitted_rect, tracking, fonts)
            finally:
                surface.set_clip(None)

        pygame.draw.rect(surface, (18, 36, 56), fitted_rect, 1)
        self.draw_corner_brackets(surface, fitted_rect, bracket_len=26, thickness=2)

        badge_top = self._draw_viewport_badges(surface, fitted_rect, telemetry, fonts, tracking)

        # Gesture layer consumes recognition results and existing landmarks, and
        # sits above the badge stack so a centred hand is never covered.
        self.gesture_overlay.render(
            surface,
            fitted_rect,
            gesture,
            tracking,
            fonts,
            (fitted_rect.right - 14, badge_top - 10),
            telemetry,
        )

        # Feedback last: a notification reports a real result and an error strip
        # offers a recovery, so neither may be hidden behind another layer.
        return self.feedback_view.render_overlay(surface, fitted_rect, telemetry, fonts)

    def _draw_viewport_badges(
        self,
        surface: pygame.Surface,
        fitted_rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        tracking: TrackingSnapshot,
    ) -> int:
        """Live feed badge, tracking engine badge and resolution readout.

        Returns the top edge of the badge stack so overlays can stack above it.
        """
        live_color = (
            int(COLOR_ONLINE[0] * self.pulse.value),
            int(COLOR_ONLINE[1] * self.pulse.value),
            int(COLOR_ONLINE[2] * self.pulse.value),
        )
        self._overlay_badge(
            surface,
            "FEED // LIVE 01",
            fonts["caption"],
            COLOR_TEXT_WHITE,
            (fitted_rect.x + 14, fitted_rect.y + 12),
            dot_color=live_color,
        )

        engine = f"{telemetry.tracking_engine} // LOCAL"
        self._overlay_badge(
            surface,
            engine,
            fonts["mono_small"],
            COLOR_ICE_BLUE,
            (fitted_rect.right - 14, fitted_rect.y + 12),
            align_right=True,
        )

        res_str = (
            f"CAM {telemetry.camera_index} | {telemetry.camera_width}x{telemetry.camera_height} | "
            f"{telemetry.camera_fps:.0f} FPS"
            if telemetry.camera_width > 0
            else "CAM IDLE"
        )
        # Bottom anchored from the badge's own height so it always sits fully
        # inside the video area, at any window size.
        badge_height = fonts["mono_small"].get_height() + 8
        resolution_rect = self._overlay_badge(
            surface,
            res_str,
            fonts["mono_small"],
            COLOR_ICE_BLUE,
            (fitted_rect.right - 14, fitted_rect.bottom - 12 - badge_height),
            align_right=True,
        )

        pipeline_str = (
            f"LANDMARK PIPELINE | {telemetry.tracker_fps:.1f} HZ | "
            f"{telemetry.tracking_latency_ms:.1f} MS"
        )
        pipeline_rect = self._overlay_badge(
            surface,
            pipeline_str,
            fonts["mono_small"],
            COLOR_TEXT_MUTED,
            (fitted_rect.right - 14, resolution_rect.top - 6),
            align_right=True,
        )

        # Performance readout: measured values only, and only while the user has
        # the diagnostics readout switched on ([P]).
        top = pipeline_rect.top
        if telemetry.diagnostics_visible:
            metrics_rect = self._overlay_badge(
                surface,
                self._performance_text(telemetry),
                fonts["mono_small"],
                COLOR_ICE_BLUE,
                (fitted_rect.right - 14, top - 6),
                align_right=True,
            )
            top = metrics_rect.top
        return top

    @staticmethod
    def _performance_text(telemetry: Telemetry) -> str:
        """FPS, tracking, gesture and control cost, with ``--`` when unmeasured.

        Every value is measured by the subsystem that owns it; a metric that has
        not been produced yet is shown as ``--`` rather than estimated.
        """

        def value(number: float, decimals: int, suffix: str) -> str:
            return f"{number:.{decimals}f} {suffix}" if number > 0 else "--"

        return (
            f"FPS {telemetry.render_fps:.0f}  |  "
            f"TRACK {value(telemetry.tracking_latency_ms, 1, 'MS')}  |  "
            f"GESTURE {value(telemetry.gesture_latency_ms, 2, 'MS')}  |  "
            f"CONTROL {value(telemetry.control_latency_ms, 2, 'MS')}"
            if telemetry.render_fps > 0
            else (
                f"FPS --  |  TRACK {value(telemetry.tracking_latency_ms, 1, 'MS')}  |  "
                f"GESTURE {value(telemetry.gesture_latency_ms, 2, 'MS')}  |  "
                f"CONTROL {value(telemetry.control_latency_ms, 2, 'MS')}"
            )
        )
