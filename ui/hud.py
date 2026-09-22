"""Futuristic HUD layout and telemetry visualizer for VisionCore."""

from __future__ import annotations

import os
from typing import Dict, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.state import SubsystemState, Telemetry
from ui.animations import PulseAnimation

# Visual Palette
COLOR_BG_DARK = (8, 12, 18)
COLOR_PANEL_BG = (13, 22, 35)
COLOR_PANEL_BORDER = (24, 46, 72)
COLOR_CYAN_PRIMARY = (0, 229, 255)
COLOR_TEAL_SECONDARY = (0, 180, 216)
COLOR_ICE_BLUE = (140, 215, 255)
COLOR_TEXT_WHITE = (235, 242, 250)
COLOR_TEXT_MUTED = (120, 145, 170)
COLOR_TEXT_DIM = (70, 90, 110)

COLOR_ONLINE = (0, 245, 160)      # Mint green
COLOR_STANDBY = (255, 183, 3)     # Amber
COLOR_DISABLED = (95, 115, 130)   # Neutral slate
COLOR_ERROR = (255, 60, 90)       # Coral red


def get_status_color(status: SubsystemState) -> Tuple[int, int, int]:
    """Return theme color matching the subsystem status."""
    if status == SubsystemState.ONLINE:
        return COLOR_ONLINE
    if status in (SubsystemState.STANDBY, SubsystemState.CHECKING, SubsystemState.INITIALIZING):
        return COLOR_STANDBY
    if status == SubsystemState.DISABLED:
        return COLOR_DISABLED
    return COLOR_ERROR


class HUDManager:
    """Manages HUD rendering, telemetry sidebars, header, and system matrix."""

    def __init__(self):
        self._pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.2)

    def update(self, dt: float) -> None:
        self._pulse.update(dt)

    def draw_chamfer_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        bg_color: Tuple[int, int, int] = COLOR_PANEL_BG,
        border_color: Tuple[int, int, int] = COLOR_PANEL_BORDER,
        chamfer: int = 8,
        border_width: int = 1,
    ) -> None:
        """Render a sci-fi chamfered corner container."""
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        c = min(chamfer, w // 4, h // 4)

        # Polygon points with chamfered top-left and bottom-right corners
        points = [
            (x + c, y),
            (x + w, y),
            (x + w, y + h - c),
            (x + w - c, y + h),
            (x, y + h),
            (x, y + c),
        ]

        # Draw filled background
        pygame.draw.polygon(surface, bg_color, points)

        # Draw tech border
        if border_width > 0:
            pygame.draw.polygon(surface, border_color, points, border_width)

        # Subtle corner highlights
        pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y + c), (x + c, y), 2)
        pygame.draw.line(
            surface,
            COLOR_CYAN_PRIMARY,
            (x + w - c, y + h),
            (x + w, y + h - c),
            2,
        )

    def draw_header(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render top navigational & branding bar."""
        # Header background strip
        pygame.draw.rect(surface, (10, 16, 26), rect)
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left, rect.bottom - 1),
            (rect.right, rect.bottom - 1),
            1,
        )

        # Glowing accent line below header
        glow_alpha = int(180 * self._pulse.value)
        accent_surf = pygame.Surface((rect.width, 2), pygame.SRCALPHA)
        accent_surf.fill((*COLOR_CYAN_PRIMARY, glow_alpha))
        surface.blit(accent_surf, (rect.left, rect.bottom - 2))

        # Brand Title
        title_surf = fonts["title"].render("VISIONCORE", True, COLOR_TEXT_WHITE)
        surface.blit(title_surf, (rect.left + 20, rect.top + 10))

        # Subtitle
        sub_surf = fonts["caption"].render("AI VISION INTERFACE // LOCAL CORE", True, COLOR_CYAN_PRIMARY)
        surface.blit(sub_surf, (rect.left + 20, rect.top + 34))

        # Right side: System Status Badge
        badge_x = rect.right - 220
        badge_y = rect.top + 14

        dot_radius = 5
        pulse_val = self._pulse.value
        dot_color = (
            int(COLOR_ONLINE[0] * pulse_val),
            int(COLOR_ONLINE[1] * pulse_val),
            int(COLOR_ONLINE[2] * pulse_val),
        )
        pygame.draw.circle(surface, dot_color, (badge_x, badge_y + 8), dot_radius)

        badge_text = fonts["subheading"].render("SYSTEM ONLINE", True, COLOR_ONLINE)
        surface.blit(badge_text, (badge_x + 14, badge_y))

        # Uptime clock
        uptime_text = fonts["mono"].render(f"UPTIME {telemetry.formatted_uptime}", True, COLOR_TEXT_MUTED)
        surface.blit(uptime_text, (badge_x + 14, badge_y + 18))

    def draw_system_matrix_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render subsystem matrix showing live and standby module states."""
        self.draw_chamfer_panel(surface, rect)

        # Panel Header
        header_surf = fonts["subheading"].render("SUBSYSTEM MATRIX", True, COLOR_CYAN_PRIMARY)
        surface.blit(header_surf, (rect.left + 16, rect.top + 12))
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left + 16, rect.top + 34),
            (rect.right - 16, rect.top + 34),
            1,
        )

        subsystems = [
            ("VISION CORE", telemetry.vision_core),
            ("CAMERA", telemetry.camera),
            ("TRACKING", telemetry.tracking),
            ("GESTURES", telemetry.gestures),
            ("DEVICE CTRL", telemetry.control),
        ]

        y_offset = rect.top + 46
        for name, status in subsystems:
            # Subsystem label
            lbl_surf = fonts["body"].render(name, True, COLOR_TEXT_WHITE)
            surface.blit(lbl_surf, (rect.left + 16, y_offset))

            # Status pill / value
            color = get_status_color(status)
            status_text = status.value
            val_surf = fonts["mono"].render(status_text, True, color)
            val_rect = val_surf.get_rect(right=rect.right - 16, centery=y_offset + 8)
            surface.blit(val_surf, val_rect)

            # Mini status indicator dot
            dot_x = val_rect.left - 10
            pygame.draw.circle(surface, color, (dot_x, y_offset + 8), 3)

            y_offset += 26

    def draw_camera_status_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render active camera stream telemetry and hardware parameters."""
        self.draw_chamfer_panel(surface, rect)

        # Header
        header_surf = fonts["subheading"].render("CAMERA TELEMETRY", True, COLOR_CYAN_PRIMARY)
        surface.blit(header_surf, (rect.left + 16, rect.top + 12))
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left + 16, rect.top + 34),
            (rect.right - 16, rect.top + 34),
            1,
        )

        metrics = [
            ("DEVICE INDEX", f"DEV #{telemetry.camera_index}"),
            ("RESOLUTION", f"{telemetry.camera_width} x {telemetry.camera_height}" if telemetry.camera_width > 0 else "N/A"),
            ("CAPTURE FPS", f"{telemetry.camera_fps:.1f} FPS" if telemetry.camera_fps > 0 else "STANDBY"),
            ("RENDER FPS", f"{telemetry.render_fps:.1f} FPS"),
            ("ORIENTATION", "MIRRORED" if telemetry.mirrored else "DIRECT"),
            ("FRAMES RECV", f"{telemetry.frame_count:,}"),
        ]

        y_offset = rect.top + 46
        for label, val in metrics:
            lbl_surf = fonts["caption"].render(label, True, COLOR_TEXT_MUTED)
            surface.blit(lbl_surf, (rect.left + 16, y_offset))

            val_surf = fonts["mono"].render(val, True, COLOR_ICE_BLUE)
            val_rect = val_surf.get_rect(right=rect.right - 16, centery=y_offset + 6)
            surface.blit(val_surf, val_rect)

            y_offset += 22

    def draw_footer_diagnostics(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render bottom status bar with engine version and hotkey prompts."""
        pygame.draw.rect(surface, (10, 15, 23), rect)
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left, rect.top),
            (rect.right, rect.top),
            1,
        )

        left_text = (
            f"LOCAL ENGINE: ACTIVE  |  SECURE: ISOLATED (NO CLOUD UPLOADS)  |  "
            f"BACKEND: {telemetry.camera_backend}"
        )
        left_surf = fonts["mono_small"].render(left_text, True, COLOR_TEXT_MUTED)
        surface.blit(left_surf, (rect.left + 16, rect.top + 7))

        right_text = "[ESC] SHUTDOWN  |  [F11] FULLSCREEN  |  [R] RECONNECT"
        right_surf = fonts["mono_small"].render(right_text, True, COLOR_CYAN_PRIMARY)
        right_rect = right_surf.get_rect(right=rect.right - 16, centery=rect.top + 14)
        surface.blit(right_surf, right_rect)
