"""Futuristic boot sequence visualizer for VisionCore."""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from ui.animations import ProgressAnimation, PulseAnimation, RotationAnimation
from ui.hud import (
    COLOR_BG_DARK,
    COLOR_CYAN_PRIMARY,
    COLOR_ERROR,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BG,
    COLOR_PANEL_BORDER,
    COLOR_STANDBY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)


class BootStep:
    """An individual step in the boot sequence with activation threshold."""

    def __init__(self, step_id: str, label: str, threshold: float):
        self.step_id = step_id
        self.label = label
        self.threshold = threshold
        self.status = "INITIALIZING"
        self.completed = False
        self.is_failure = False


class BootScreen:
    """Renders the sci-fi system initialization boot sequence."""

    def __init__(self, duration_sec: float = 2.4):
        self.duration = duration_sec
        self.progress_anim = ProgressAnimation(duration=duration_sec)
        self.rot_anim = RotationAnimation(speed_deg_per_sec=55.0)
        self.pulse = PulseAnimation(min_val=0.3, max_val=1.0, frequency_hz=1.8)

        self.steps: List[BootStep] = [
            BootStep("01", "CORE ARCHITECTURE", 0.15),
            BootStep("02", "DISPLAY SUBSYSTEM", 0.35),
            BootStep("03", "CAMERA DEVICE PROBE", 0.60),
            BootStep("04", "VISION ENGINE", 0.80),
            BootStep("05", "TRACKING PIPELINE", 0.95),
        ]

        self.camera_check_passed: Optional[bool] = None
        self.camera_check_message: str = "CHECKING..."
        self.is_complete = False

    def notify_camera_result(self, success: bool, message: str) -> None:
        """Receive asynchronous camera probe status during the boot sequence."""
        self.camera_check_passed = success
        self.camera_check_message = message
        # Update step 03 state
        for step in self.steps:
            if step.step_id == "03":
                if success:
                    step.status = "ONLINE"
                    step.completed = True
                    step.is_failure = False
                else:
                    step.status = "UNAVAILABLE"
                    step.completed = True
                    step.is_failure = True

    def update(self, dt: float) -> bool:
        """
        Advance boot animations.
        Returns True when boot sequence animation has concluded.
        """
        progress = self.progress_anim.update(dt)
        self.rot_anim.update(dt)
        self.pulse.update(dt)

        for step in self.steps:
            if progress >= step.threshold and not step.completed:
                if step.step_id == "03":
                    # Camera step status depends on probe outcome
                    if self.camera_check_passed is True:
                        step.status = "ONLINE"
                        step.completed = True
                    elif self.camera_check_passed is False:
                        step.status = "UNAVAILABLE"
                        step.completed = True
                        step.is_failure = True
                    else:
                        step.status = "CHECKING..."
                elif step.step_id == "05":
                    step.status = "STANDBY"
                    step.completed = True
                else:
                    step.status = "READY"
                    step.completed = True

        if self.progress_anim.is_complete and not self.is_complete:
            self.is_complete = True
            return True

        return self.is_complete

    def draw_tech_emblem(
        self,
        surface: pygame.Surface,
        center: Tuple[int, int],
        radius: int = 42,
    ) -> None:
        """Draw rotating geometric sci-fi emblem in the center."""
        cx, cy = center
        angle = math.radians(self.rot_anim.angle)

        emblem_surf = pygame.Surface((radius * 2 + 16, radius * 2 + 16), pygame.SRCALPHA)
        ec = radius + 8

        # Outer segmented ring
        num_segments = 6
        for i in range(num_segments):
            start_th = angle + i * (2 * math.pi / num_segments)
            end_th = start_th + (math.pi / (num_segments * 1.5))
            pts = []
            for step in range(6):
                th = start_th + (end_th - start_th) * (step / 5)
                pts.append((ec + int(radius * math.cos(th)), ec + int(radius * math.sin(th))))
            if len(pts) >= 2:
                pygame.draw.lines(emblem_surf, (*COLOR_CYAN_PRIMARY, 200), False, pts, 2)

        # Counter-rotating inner hexagon
        inner_angle = -angle * 1.2
        hex_radius = radius - 14
        hex_pts = []
        for i in range(6):
            th = inner_angle + i * (math.pi / 3)
            hex_pts.append((ec + int(hex_radius * math.cos(th)), ec + int(hex_radius * math.sin(th))))
        pygame.draw.polygon(emblem_surf, (*COLOR_ICE_BLUE, 160), hex_pts, 1)

        # Pulsing center core
        core_r = int(7 * self.pulse.value)
        pygame.draw.circle(emblem_surf, COLOR_CYAN_PRIMARY, (ec, ec), max(2, core_r))

        surface.blit(emblem_surf, (cx - ec, cy - ec))

    def render(
        self,
        surface: pygame.Surface,
        screen_rect: pygame.Rect,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render the complete futuristic boot sequence frame."""
        surface.fill(COLOR_BG_DARK)

        card_w = min(680, screen_rect.width - 40)
        card_h = 440
        card_x = screen_rect.centerx - card_w // 2
        card_y = screen_rect.centery - card_h // 2
        card_rect = pygame.Rect(card_x, card_y, card_w, card_h)

        # Card background & border
        pygame.draw.rect(surface, COLOR_PANEL_BG, card_rect)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, card_rect, 1)

        # Corner brackets
        b_len = 20
        c_col = COLOR_CYAN_PRIMARY
        # TL
        pygame.draw.line(surface, c_col, (card_x, card_y), (card_x + b_len, card_y), 2)
        pygame.draw.line(surface, c_col, (card_x, card_y), (card_x, card_y + b_len), 2)
        # TR
        pygame.draw.line(surface, c_col, (card_x + card_w, card_y), (card_x + card_w - b_len, card_y), 2)
        pygame.draw.line(surface, c_col, (card_x + card_w, card_y), (card_x + card_w, card_y + b_len), 2)
        # BL
        pygame.draw.line(surface, c_col, (card_x, card_y + card_h), (card_x + b_len, card_y + card_h), 2)
        pygame.draw.line(surface, c_col, (card_x, card_y + card_h), (card_x, card_y + card_h - b_len), 2)
        # BR
        pygame.draw.line(surface, c_col, (card_x + card_w, card_y + card_h), (card_x + card_w - b_len, card_y + card_h), 2)
        pygame.draw.line(surface, c_col, (card_x + card_w, card_y + card_h), (card_x + card_w, card_y + card_h - b_len), 2)

        # Emblem
        self.draw_tech_emblem(surface, (card_rect.centerx, card_y + 60), radius=38)

        # Title
        title_surf = fonts["title"].render("VISIONCORE", True, COLOR_TEXT_WHITE)
        title_rect = title_surf.get_rect(center=(card_rect.centerx, card_y + 120))
        surface.blit(title_surf, title_rect)

        # Subtitle
        sub_surf = fonts["caption"].render("INITIALIZING VISION SYSTEM", True, COLOR_CYAN_PRIMARY)
        sub_rect = sub_surf.get_rect(center=(card_rect.centerx, card_y + 144))
        surface.blit(sub_surf, sub_rect)

        # Horizontal separator
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (card_x + 30, card_y + 162),
            (card_x + card_w - 30, card_y + 162),
            1,
        )

        # Step-by-step diagnostic list
        y_step = card_y + 178
        for step in self.steps:
            # Step number
            num_surf = fonts["mono"].render(f"[{step.step_id}]", True, COLOR_CYAN_PRIMARY)
            surface.blit(num_surf, (card_x + 40, y_step))

            # Step label
            lbl_surf = fonts["body"].render(step.label, True, COLOR_TEXT_WHITE)
            surface.blit(lbl_surf, (card_x + 85, y_step))

            # Dots filler
            dots_x = card_x + 290
            dots_w = (card_x + card_w - 180) - dots_x
            if dots_w > 10:
                dots_str = "." * (dots_w // 8)
                dots_surf = fonts["mono"].render(dots_str, True, (35, 55, 80))
                surface.blit(dots_surf, (dots_x, y_step))

            # Status value
            if step.is_failure:
                val_color = COLOR_ERROR
            elif step.completed:
                val_color = COLOR_ONLINE if step.status in ("ONLINE", "READY") else COLOR_STANDBY
            else:
                val_color = COLOR_TEXT_MUTED

            val_surf = fonts["mono"].render(step.status, True, val_color)
            val_rect = val_surf.get_rect(right=card_x + card_w - 40, top=y_step)
            surface.blit(val_surf, val_rect)

            y_step += 25

        # Progress bar
        bar_y = card_y + card_h - 52
        bar_w = card_w - 80
        bar_h = 6
        bar_x = card_x + 40

        # Background track
        pygame.draw.rect(surface, (18, 30, 46), (bar_x, bar_y, bar_w, bar_h))

        # Filled portion
        fill_w = int(bar_w * self.progress_anim.progress)
        if fill_w > 0:
            fill_color = COLOR_ERROR if (self.camera_check_passed is False and self.progress_anim.progress > 0.8) else COLOR_CYAN_PRIMARY
            pygame.draw.rect(surface, fill_color, (bar_x, bar_y, fill_w, bar_h))

        # Progress percentage text
        pct_text = f"{int(self.progress_anim.progress * 100):3d}%"
        pct_surf = fonts["mono_small"].render(pct_text, True, COLOR_ICE_BLUE)
        pct_rect = pct_surf.get_rect(right=card_x + card_w - 40, bottom=bar_y - 4)
        surface.blit(pct_surf, pct_rect)

        # Status footer string
        if self.is_complete:
            if self.camera_check_passed is False:
                msg = "CAMERA PROBE FAILED // TRANSITIONING TO ERROR RECOVERY"
                msg_col = COLOR_ERROR
            else:
                msg = "SYSTEM READY // INITIALIZING CAMERA HUD"
                msg_col = COLOR_ONLINE
        else:
            msg = "CORE TELEMETRY VALIDATION IN PROGRESS..."
            msg_col = COLOR_TEXT_MUTED

        stat_surf = fonts["mono_small"].render(msg, True, msg_col)
        surface.blit(stat_surf, (bar_x, bar_y + 12))
