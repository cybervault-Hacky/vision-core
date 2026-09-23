"""Camera viewport renderer, letterbox scaling, and futuristic scanning HUD."""

from __future__ import annotations

import math
import os
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.gestures import GestureSnapshot
from app.hand_tracking import TrackingSnapshot
from app.state import Telemetry
from ui.animations import PulseAnimation, RotationAnimation
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


class CameraView:
    """Renders the video viewport with aspect-ratio preservation and HUD overlays."""

    def __init__(self) -> None:
        self.reticle_rot = RotationAnimation(speed_deg_per_sec=22.0)
        self.pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.0)
        self.hand_overlay = HandOverlay()
        self.gesture_overlay = GestureOverlay()

        # Cached surface for the scaled video frame
        self._last_frame_surf: Optional[pygame.Surface] = None

    def update(
        self,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> None:
        """Advance animation states."""
        self.reticle_rot.update(dt)
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

    def draw_targeting_reticle(
        self,
        surface: pygame.Surface,
        center: Tuple[int, int],
        radius: int = 40,
    ) -> None:
        """Rotating circular telemetry reticle used while searching for a hand."""
        cx, cy = center
        angle_rad = math.radians(self.reticle_rot.angle)
        rc = radius + 4
        reticle_surf = pygame.Surface((rc * 2, rc * 2), pygame.SRCALPHA)

        pygame.draw.circle(reticle_surf, (*COLOR_CYAN_PRIMARY, 55), (rc, rc), radius, 1)

        for i in range(4):
            start_a = angle_rad + i * (math.pi / 2)
            end_a = start_a + math.pi / 4
            pts = [
                (
                    rc + int((radius + 2) * math.cos(start_a + (end_a - start_a) * (step / 7))),
                    rc + int((radius + 2) * math.sin(start_a + (end_a - start_a) * (step / 7))),
                )
                for step in range(8)
            ]
            pygame.draw.lines(reticle_surf, (*COLOR_CYAN_PRIMARY, 150), False, pts, 2)

        tick = 6
        for a, b in (
            ((rc - tick, rc), (rc - 2, rc)),
            ((rc + 2, rc), (rc + tick, rc)),
            ((rc, rc - tick), (rc, rc - 2)),
            ((rc, rc + 2), (rc, rc + tick)),
        ):
            pygame.draw.line(reticle_surf, (*COLOR_ICE_BLUE, 170), a, b, 1)

        surface.blit(reticle_surf, (cx - rc, cy - rc))

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
    ) -> None:
        """Render the complete camera viewport, video surface, and HUD layers."""
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

            # Hand tracking layer reacts to the actual tracking state
            self.hand_overlay.render(surface, fitted_rect, tracking, fonts)



            # Ambient reticle only while the pipeline has nothing locked
            if not tracking.state.is_engaged:
                self.draw_targeting_reticle(
                    surface,
                    (fitted_rect.centerx, fitted_rect.centery),
                    radius=40,
                )

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
        )

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
        resolution_rect = self._overlay_badge(
            surface,
            res_str,
            fonts["mono_small"],
            COLOR_ICE_BLUE,
            (fitted_rect.right - 14, fitted_rect.bottom - 12),
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
        return pipeline_rect.top
