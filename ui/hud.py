"""Futuristic HUD layout and telemetry visualizer for VisionCore."""

from __future__ import annotations

import os
from typing import Dict, List, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.state import SubsystemState, Telemetry
from ui.animations import PulseAnimation

# Visual Palette
COLOR_BG_DARK = (8, 12, 18)
COLOR_PANEL_BG = (13, 22, 35)
COLOR_PANEL_BORDER = (24, 46, 72)
COLOR_CYAN_PRIMARY = (0, 229, 255)
COLOR_ICE_BLUE = (140, 215, 255)
COLOR_TEXT_WHITE = (235, 242, 250)
COLOR_TEXT_MUTED = (120, 145, 170)

COLOR_ONLINE = (0, 245, 160)      # Mint green
COLOR_STANDBY = (255, 183, 3)     # Amber
COLOR_DISABLED = (95, 115, 130)   # Neutral slate
COLOR_ERROR = (255, 60, 90)       # Coral red

def get_status_color(status: SubsystemState) -> Tuple[int, int, int]:
    """Return theme color matching the subsystem status."""
    if status in (SubsystemState.ONLINE, SubsystemState.ACTIVE):
        return COLOR_ONLINE
    if status in (SubsystemState.STANDBY, SubsystemState.CHECKING, SubsystemState.INITIALIZING):
        return COLOR_STANDBY
    if status is SubsystemState.SEARCHING:
        return COLOR_ICE_BLUE
    if status is SubsystemState.LOST:
        return COLOR_STANDBY
    if status is SubsystemState.DISABLED:
        return COLOR_DISABLED
    return COLOR_ERROR


class HUDManager:
    """Manages HUD rendering, telemetry sidebars, header, and system matrix."""

    def __init__(self) -> None:
        self._pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.2)

    def update(self, dt: float) -> None:
        self._pulse.update(dt)

    # -- containers -------------------------------------------------------- #

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

        pygame.draw.polygon(surface, bg_color, points)

        if border_width > 0:
            pygame.draw.polygon(surface, border_color, points, border_width)

        pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y + c), (x + c, y), 2)
        pygame.draw.line(
            surface,
            COLOR_CYAN_PRIMARY,
            (x + w - c, y + h),
            (x + w, y + h - c),
            2,
        )

    def draw_panel_header(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        title: str,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render a panel title and its divider."""
        header_surf = fonts["subheading"].render(title, True, COLOR_CYAN_PRIMARY)
        surface.blit(header_surf, (rect.left + 16, rect.top + 12))
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left + 16, rect.top + 34),
            (rect.right - 16, rect.top + 34),
            1,
        )

    def draw_metric_rows(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        rows: Sequence[Tuple[str, str, Tuple[int, int, int]]],
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Draw label/value rows with spacing that adapts to the panel height."""
        top = rect.top + 46
        available = max(1, (rect.bottom - 14) - top)
        step = max(16, min(24, available // max(1, len(rows))))
        y = top

        for label, value, color in rows:
            label_surf = fonts["caption"].render(label, True, COLOR_TEXT_MUTED)
            surface.blit(label_surf, (rect.left + 16, y))

            value_surf = fonts["mono"].render(value, True, color)
            value_rect = value_surf.get_rect(right=rect.right - 16, centery=y + 7)
            surface.blit(value_surf, value_rect)
            y += step

    # -- header & footer --------------------------------------------------- #

    def draw_header(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render top navigational & branding bar."""
        pygame.draw.rect(surface, (10, 16, 26), rect)
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left, rect.bottom - 1),
            (rect.right, rect.bottom - 1),
            1,
        )

        glow_alpha = int(180 * self._pulse.value)
        accent_surf = pygame.Surface((rect.width, 2), pygame.SRCALPHA)
        accent_surf.fill((*COLOR_CYAN_PRIMARY, glow_alpha))
        surface.blit(accent_surf, (rect.left, rect.bottom - 2))

        title_surf = fonts["title"].render("VISIONCORE", True, COLOR_TEXT_WHITE)
        surface.blit(title_surf, (rect.left + 20, rect.top + 10))

        sub_surf = fonts["caption"].render(
            "AI VISION INTERFACE // LOCAL CORE", True, COLOR_CYAN_PRIMARY
        )
        surface.blit(sub_surf, (rect.left + 20, rect.top + 34))

        badge_x = rect.right - 250
        badge_y = rect.top + 12

        dot_color = tuple(int(channel * self._pulse.value) for channel in COLOR_ONLINE)
        pygame.draw.circle(surface, dot_color, (badge_x, badge_y + 8), 5)

        badge_text = fonts["subheading"].render("SYSTEM ONLINE", True, COLOR_ONLINE)
        surface.blit(badge_text, (badge_x + 14, badge_y))

        uptime_text = fonts["mono"].render(
            f"UPTIME {telemetry.formatted_uptime}", True, COLOR_TEXT_MUTED
        )
        surface.blit(uptime_text, (badge_x + 14, badge_y + 20))

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

    # -- panels ------------------------------------------------------------ #

    def draw_system_matrix_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render subsystem matrix showing live and standby module states."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "SUBSYSTEM MATRIX", fonts)

        subsystems = [
            ("VISION CORE", telemetry.vision_core),
            ("CAMERA", telemetry.camera),
            ("TRACKING", telemetry.tracking),
            ("GESTURES", telemetry.gestures),
            ("CONTROL", telemetry.control),
        ]

        rows: List[Tuple[str, str, Tuple[int, int, int]]] = []
        for name, status in subsystems:
            rows.append((name, status.value, get_status_color(status)))

        top = rect.top + 46
        available = max(1, (rect.bottom - 14) - top)
        step = max(18, min(26, available // max(1, len(rows))))
        y = top

        for name, status, color in rows:
            label_surf = fonts["body"].render(name, True, COLOR_TEXT_WHITE)
            surface.blit(label_surf, (rect.left + 16, y))

            value_surf = fonts["mono"].render(status, True, color)
            value_rect = value_surf.get_rect(right=rect.right - 16, centery=y + 8)
            surface.blit(value_surf, value_rect)

            pygame.draw.circle(surface, color, (value_rect.left - 10, y + 8), 3)
            y += step

    def draw_tracking_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render live hand tracking telemetry (measured values only)."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "HAND TRACKING", fonts)

        state_label = telemetry.tracking_state.status_label
        state_color = get_status_color(telemetry.tracking)

        confidence = (
            f"{telemetry.hand_confidence * 100:.1f} %"
            if telemetry.hand_confidence is not None
            else "--"
        )

        rows = [
            ("STATE", state_label, state_color),
            ("HANDS", f"{telemetry.hands_detected} / {telemetry.max_hands}", COLOR_ICE_BLUE),
            ("HANDEDNESS", telemetry.hand_handedness or "--", COLOR_TEXT_WHITE),
            ("CONFIDENCE", confidence, COLOR_ICE_BLUE),
            (
                "INFERENCE",
                f"{telemetry.tracking_latency_ms:.1f} MS" if telemetry.tracking_latency_ms > 0 else "--",
                COLOR_TEXT_MUTED,
            ),
            (
                "PIPELINE",
                f"{telemetry.tracker_fps:.1f} HZ" if telemetry.tracker_fps > 0 else "--",
                COLOR_TEXT_MUTED,
            ),
        ]

        # Only surfaced when the host cannot keep up with the tracker.
        if telemetry.tracking_dropped_frames > 0:
            rows.append(("DROPPED", str(telemetry.tracking_dropped_frames), COLOR_STANDBY))

        self.draw_metric_rows(surface, rect, rows, fonts)

    def draw_camera_status_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render active camera stream telemetry and hardware parameters."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "CAMERA TELEMETRY", fonts)

        rows = [
            ("DEVICE INDEX", f"DEV #{telemetry.camera_index}", COLOR_ICE_BLUE),
            (
                "RESOLUTION",
                f"{telemetry.camera_width} x {telemetry.camera_height}"
                if telemetry.camera_width > 0
                else "N/A",
                COLOR_ICE_BLUE,
            ),
            (
                "CAPTURE FPS",
                f"{telemetry.camera_fps:.1f} FPS" if telemetry.camera_fps > 0 else "STANDBY",
                COLOR_ICE_BLUE,
            ),
            ("RENDER FPS", f"{telemetry.render_fps:.1f} FPS", COLOR_ICE_BLUE),
            ("ORIENTATION", "MIRRORED" if telemetry.mirrored else "DIRECT", COLOR_ICE_BLUE),
            ("FRAMES RECV", f"{telemetry.frame_count:,}", COLOR_TEXT_MUTED),
        ]
        self.draw_metric_rows(surface, rect, rows, fonts)
