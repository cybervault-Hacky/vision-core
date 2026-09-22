"""Camera viewport renderer, letterbox scaling, and futuristic scanning HUD."""

from __future__ import annotations

import math
import os
from typing import Dict, Optional, Tuple

import numpy as np
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.state import Telemetry
from ui.animations import PulseAnimation, RotationAnimation, ScanlineAnimation
from ui.hud import (
    COLOR_BG_DARK,
    COLOR_CYAN_PRIMARY,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BG,
    COLOR_PANEL_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)


class CameraView:
    """Renders the video viewport with aspect-ratio preservation and futuristic overlays."""

    def __init__(self):
        self.scanline = ScanlineAnimation(speed=0.35)
        self.reticle_rot = RotationAnimation(speed_deg_per_sec=25.0)
        self.pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.0)

        # Cached surface for scaled video frame
        self._cached_frame_surf: Optional[pygame.Surface] = None
        self._last_frame_shape: Optional[Tuple[int, int]] = None
        self._last_target_size: Optional[Tuple[int, int]] = None

    def update(self, dt: float) -> None:
        """Advance animation states."""
        self.scanline.update(dt)
        self.reticle_rot.update(dt)
        self.pulse.update(dt)

    def _calculate_letterbox(
        self,
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
            # Container is wider: pillarbox (black bars on left/right)
            scaled_h = container_rect.height
            scaled_w = int(scaled_h * frame_aspect)
            scaled_x = container_rect.x + (container_rect.width - scaled_w) // 2
            scaled_y = container_rect.y
        else:
            # Container is taller: letterbox (black bars on top/bottom)
            scaled_w = container_rect.width
            scaled_h = int(scaled_w / frame_aspect)
            scaled_x = container_rect.x
            scaled_y = container_rect.y + (container_rect.height - scaled_h) // 2

        return pygame.Rect(scaled_x, scaled_y, scaled_w, scaled_h)

    def draw_corner_brackets(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        bracket_len: int = 24,
        thickness: int = 2,
    ) -> None:
        """Render futuristic geometric corner brackets around a viewport."""
        b_len = min(bracket_len, rect.width // 4, rect.height // 4)
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        color = COLOR_CYAN_PRIMARY

        # Top-Left Bracket
        pygame.draw.line(surface, color, (x, y), (x + b_len, y), thickness)
        pygame.draw.line(surface, color, (x, y), (x, y + b_len), thickness)

        # Top-Right Bracket
        pygame.draw.line(surface, color, (x + w, y), (x + w - b_len, y), thickness)
        pygame.draw.line(surface, color, (x + w, y), (x + w, y + b_len), thickness)

        # Bottom-Left Bracket
        pygame.draw.line(surface, color, (x, y + h), (x + b_len, y + h), thickness)
        pygame.draw.line(surface, color, (x, y + h), (x, y + h - b_len), thickness)

        # Bottom-Right Bracket
        pygame.draw.line(surface, color, (x + w, y + h), (x + w - b_len, y + h), thickness)
        pygame.draw.line(surface, color, (x + w, y + h), (x + w, y + h - b_len), thickness)

    def draw_scanning_line(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
    ) -> None:
        """Render a vertical sweeping laser scanline over the feed."""
        scan_y = int(rect.y + self.scanline.position * rect.height)
        if not (rect.y <= scan_y <= rect.bottom):
            return

        # Semi-transparent scan beam
        beam_h = 24
        beam_surf = pygame.Surface((rect.width, beam_h), pygame.SRCALPHA)
        for i in range(beam_h):
            alpha = int(45 * (1.0 - (i / beam_h)))
            pygame.draw.line(beam_surf, (*COLOR_CYAN_PRIMARY, alpha), (0, i), (rect.width, i))
        surface.blit(beam_surf, (rect.x, max(rect.y, scan_y - beam_h)))

        # Bright leading edge
        pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (rect.x, scan_y), (rect.right, scan_y), 1)

    def draw_targeting_reticle(
        self,
        surface: pygame.Surface,
        center: Tuple[int, int],
        radius: int = 36,
    ) -> None:
        """Render subtle rotating circular telemetry reticle in center."""
        cx, cy = center
        angle_rad = math.radians(self.reticle_rot.angle)

        # Outer subtle ring
        reticle_surf = pygame.Surface((radius * 2 + 8, radius * 2 + 8), pygame.SRCALPHA)
        rc = radius + 4
        pygame.draw.circle(reticle_surf, (*COLOR_CYAN_PRIMARY, 60), (rc, rc), radius, 1)

        # Segmented arcs
        num_segments = 4
        arc_span = math.pi / 4
        for i in range(num_segments):
            start_a = angle_rad + i * (math.pi / 2)
            end_a = start_a + arc_span
            # Draw arc approximation points
            pts = []
            for step in range(8):
                theta = start_a + (end_a - start_a) * (step / 7)
                px = rc + int((radius + 2) * math.cos(theta))
                py = rc + int((radius + 2) * math.sin(theta))
                pts.append((px, py))
            if len(pts) >= 2:
                pygame.draw.lines(reticle_surf, (*COLOR_CYAN_PRIMARY, 160), False, pts, 2)

        # Center crosshair ticks
        tick_len = 6
        pygame.draw.line(reticle_surf, (*COLOR_ICE_BLUE, 180), (rc - tick_len, rc), (rc - 2, rc), 1)
        pygame.draw.line(reticle_surf, (*COLOR_ICE_BLUE, 180), (rc + 2, rc), (rc + tick_len, rc), 1)
        pygame.draw.line(reticle_surf, (*COLOR_ICE_BLUE, 180), (rc, rc - tick_len), (rc, rc - 2), 1)
        pygame.draw.line(reticle_surf, (*COLOR_ICE_BLUE, 180), (rc, rc + 2), (rc, rc + tick_len), 1)

        surface.blit(reticle_surf, (cx - rc, cy - rc))

    def render(
        self,
        surface: pygame.Surface,
        viewport_rect: pygame.Rect,
        frame: Optional[np.ndarray],
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render complete camera viewport, video surface, and HUD animations."""
        # Dark viewport well
        pygame.draw.rect(surface, (5, 8, 12), viewport_rect)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, viewport_rect, 1)

        fitted_rect = viewport_rect

        if frame is not None and frame.size > 0:
            fh, fw = frame.shape[:2]
            fitted_rect = self._calculate_letterbox(fw, fh, viewport_rect)

            # Convert numpy array to Pygame Surface
            frame_surf = pygame.image.frombuffer(frame.tobytes(), (fw, fh), "RGB")

            # Scale to fit aspect-ratio preserving rectangle
            if (fitted_rect.width, fitted_rect.height) != (fw, fh):
                scaled_surf = pygame.transform.smoothscale(
                    frame_surf,
                    (fitted_rect.width, fitted_rect.height),
                )
            else:
                scaled_surf = frame_surf

            # Blit camera frame
            surface.blit(scaled_surf, (fitted_rect.x, fitted_rect.y))

            # Scanning animation over active feed
            self.draw_scanning_line(surface, fitted_rect)

            # Center subtle reticle
            center = (fitted_rect.centerx, fitted_rect.centery)
            self.draw_targeting_reticle(surface, center, radius=40)

        # Frame border around the active image area
        pygame.draw.rect(surface, (18, 36, 56), fitted_rect, 1)
        self.draw_corner_brackets(surface, fitted_rect, bracket_len=26, thickness=2)

        # Top Overlay: LIVE FEED badge
        badge_rect = pygame.Rect(fitted_rect.x + 14, fitted_rect.y + 12, 140, 24)
        badge_bg = pygame.Surface((badge_rect.width, badge_rect.height), pygame.SRCALPHA)
        badge_bg.fill((10, 18, 28, 200))
        surface.blit(badge_bg, badge_rect)
        pygame.draw.rect(surface, (28, 56, 84), badge_rect, 1)

        # Pulsing Live Dot
        dot_color = (
            int(COLOR_ONLINE[0] * self.pulse.value),
            int(COLOR_ONLINE[1] * self.pulse.value),
            int(COLOR_ONLINE[2] * self.pulse.value),
        )
        pygame.draw.circle(surface, dot_color, (badge_rect.x + 12, badge_rect.centery), 4)

        tag_surf = fonts["caption"].render("FEED // LIVE 01", True, COLOR_TEXT_WHITE)
        surface.blit(tag_surf, (badge_rect.x + 22, badge_rect.y + 5))

        # Bottom Overlay: Resolution & Aspect Info
        res_str = (
            f"CAM {telemetry.camera_index} | {telemetry.camera_width}x{telemetry.camera_height} | "
            f"{telemetry.camera_fps:.0f} FPS"
            if telemetry.camera_width > 0
            else "CAM IDLE"
        )
        info_surf = fonts["mono_small"].render(res_str, True, COLOR_ICE_BLUE)
        info_rect = info_surf.get_rect(right=fitted_rect.right - 14, bottom=fitted_rect.bottom - 12)

        info_bg = pygame.Surface((info_rect.width + 12, info_rect.height + 6), pygame.SRCALPHA)
        info_bg.fill((10, 18, 28, 200))
        surface.blit(info_bg, (info_rect.x - 6, info_rect.y - 3))
        pygame.draw.rect(surface, (28, 56, 84), (info_rect.x - 6, info_rect.y - 3, info_rect.width + 12, info_rect.height + 6), 1)
        surface.blit(info_surf, info_rect)
