"""VisionCore startup sequence.

Original identity, restrained visual language, and honest reporting: each step
shows the real result of the subsystem it names. The camera probe and the
tracking engine are asynchronous, so their steps stay at ``CHECKING``/``LOADING``
until the application reports what actually happened - the sequence never claims
a subsystem is ready before it is.

The sequence is deliberately short, ends on a real ``SYSTEM READY`` or
``RECOVERY REQUIRED`` result, and holds for a moment so the transition into the
camera interface reads as intentional.
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from ui.animations import ProgressAnimation, PulseAnimation, RotationAnimation
from version import DISPLAY_VERSION
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

# Short hold on "SYSTEM READY" before the camera interface takes over.
READY_HOLD_SEC = 0.55


class BootStep:
    """One startup step with its activation threshold and real status."""

    def __init__(self, step_id: str, label: str, threshold: float):
        self.step_id = step_id
        self.label = label
        self.threshold = threshold
        self.status = "INITIALIZING"
        self.completed = False
        self.is_failure = False


class BootScreen:
    """Renders the VisionCore system initialization sequence."""

    def __init__(self, duration_sec: float = 2.4):
        self.duration = duration_sec
        self.progress_anim = ProgressAnimation(duration=duration_sec)
        self.rot_anim = RotationAnimation(speed_deg_per_sec=45.0)
        self.pulse = PulseAnimation(min_val=0.3, max_val=1.0, frequency_hz=1.6)

        # Order matters: it is the order the interface genuinely comes up in.
        self.steps: List[BootStep] = [
            BootStep("01", "CAMERA", 0.18),
            BootStep("02", "TRACKING", 0.42),
            BootStep("03", "GESTURE ENGINE", 0.62),
            BootStep("04", "CONTROL LAYER", 0.78),
            BootStep("05", "HUD", 0.88),
        ]

        self.camera_check_passed: Optional[bool] = None
        self.tracking_check_passed: Optional[bool] = None
        self.gesture_check_passed: Optional[bool] = None
        self.control_check_passed: Optional[bool] = None

        self.is_complete = False
        self._ready_since: Optional[float] = None

    # -- asynchronous results --------------------------------------------- #

    def notify_camera_result(self, success: bool, message: str) -> None:
        """Camera probe finished (real result of opening the device)."""
        self.camera_check_passed = success
        self._settle("01", "READY" if success else "UNAVAILABLE")

    def notify_tracking_result(self, success: bool, message: str) -> None:
        """Hand tracking engine finished loading, or reported why it could not."""
        self.tracking_check_passed = success
        disabled = "DISABLED" in (message or "").upper()
        status = "READY" if success else ("DISABLED" if disabled else "UNAVAILABLE")
        self._settle("02", status)

    def notify_gesture_result(self, success: bool, message: str = "") -> None:
        """Gesture recognition engine availability."""
        self.gesture_check_passed = success
        self._settle("03", "READY" if success else "DISABLED")

    def notify_control_result(self, success: bool, message: str = "") -> None:
        """Control layer availability (the backend really reports this)."""
        self.control_check_passed = success
        self._settle("04", "READY" if success else "UNAVAILABLE")

    def _settle(self, step_id: str, status: str) -> None:
        """Record a real subsystem result verbatim."""
        for step in self.steps:
            if step.step_id == step_id:
                step.status = status
                step.completed = True
                step.is_failure = status == "UNAVAILABLE"

    # -- sequence ---------------------------------------------------------- #

    def update(self, dt: float) -> bool:
        """Advance the sequence. Returns True once it is finished."""
        progress = self.progress_anim.update(dt)
        self.rot_anim.update(dt)
        self.pulse.update(dt)

        for step in self.steps:
            if step.completed or progress < step.threshold:
                continue
            if step.step_id == "01":
                status = self._pending_status(self.camera_check_passed, "CHECKING...")
            elif step.step_id == "02":
                status = self._pending_status(self.tracking_check_passed, "LOADING...")
            elif step.step_id == "03":
                status = self._pending_status(self.gesture_check_passed, "LOADING...")
            elif step.step_id == "04":
                status = self._pending_status(self.control_check_passed, "PROBING...")
            else:
                # The HUD is up the moment it is drawn: that is a real result.
                status = "READY"
            if status in ("READY", "DISABLED", "UNAVAILABLE"):
                step.status = status
                step.completed = True
                step.is_failure = status == "UNAVAILABLE"
            else:
                step.status = status

        if not self.progress_anim.is_complete:
            return False
        # The progress bar is presentation only. Do not leave boot until every
        # asynchronous check has reported a real result; a slow camera or
        # tracker must remain CHECKING/LOADING rather than becoming READY.
        if not all(step.completed for step in self.steps):
            return False
        if self._ready_since is None:
            self._ready_since = 0.0
        self._ready_since += dt
        self.is_complete = self._ready_since >= READY_HOLD_SEC
        return self.is_complete

    def _pending_status(self, result: Optional[bool], pending: str) -> str:
        """Status for a step whose asynchronous result has not arrived yet."""
        if result is None:
            return pending
        return "READY" if result else "UNAVAILABLE"

    @property
    def ready(self) -> bool:
        """True once every step settled with a genuine READY."""
        return all(step.completed and step.status == "READY" for step in self.steps)

    # -- rendering --------------------------------------------------------- #

    def draw_tech_emblem(
        self,
        surface: pygame.Surface,
        center: Tuple[int, int],
        radius: int = 42,
    ) -> None:
        """Draw the rotating geometric emblem of the VisionCore identity."""
        cx, cy = center
        angle = math.radians(self.rot_anim.angle)

        emblem_surf = pygame.Surface((radius * 2 + 16, radius * 2 + 16), pygame.SRCALPHA)
        ec = radius + 8

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

        inner_angle = -angle * 1.2
        hex_radius = radius - 14
        hex_pts = []
        for i in range(6):
            th = inner_angle + i * (math.pi / 3)
            hex_pts.append((ec + int(hex_radius * math.cos(th)), ec + int(hex_radius * math.sin(th))))
        pygame.draw.polygon(emblem_surf, (*COLOR_ICE_BLUE, 160), hex_pts, 1)

        core_r = int(7 * self.pulse.value)
        pygame.draw.circle(emblem_surf, COLOR_CYAN_PRIMARY, (ec, ec), max(2, core_r))

        surface.blit(emblem_surf, (cx - ec, cy - ec))

    def render(
        self,
        surface: pygame.Surface,
        screen_rect: pygame.Rect,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render the complete startup sequence frame."""
        surface.fill(COLOR_BG_DARK)

        card_w = min(680, screen_rect.width - 40)
        card_h = 420
        card_x = screen_rect.centerx - card_w // 2
        card_y = screen_rect.centery - card_h // 2
        card_rect = pygame.Rect(card_x, card_y, card_w, card_h)

        pygame.draw.rect(surface, COLOR_PANEL_BG, card_rect)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, card_rect, 1)

        b_len = 20
        c_col = COLOR_CYAN_PRIMARY
        for x, y, dx, dy in (
            (card_x, card_y, 1, 1),
            (card_x + card_w, card_y, -1, 1),
            (card_x, card_y + card_h, 1, -1),
            (card_x + card_w, card_y + card_h, -1, -1),
        ):
            pygame.draw.line(surface, c_col, (x, y), (x + b_len * dx, y), 2)
            pygame.draw.line(surface, c_col, (x, y), (x, y + b_len * dy), 2)

        self.draw_tech_emblem(surface, (card_rect.centerx, card_y + 58), radius=36)

        title_surf = fonts["title"].render(DISPLAY_VERSION.upper(), True, COLOR_TEXT_WHITE)
        surface.blit(title_surf, title_surf.get_rect(center=(card_rect.centerx, card_y + 116)))

        failure = any(step.is_failure for step in self.steps)
        if self.is_complete and not failure:
            subtitle, subtitle_color = "SYSTEM READY", COLOR_ONLINE
        elif failure and self.progress_anim.is_complete:
            subtitle, subtitle_color = "RECOVERY REQUIRED", COLOR_ERROR
        else:
            subtitle, subtitle_color = "SUBSYSTEM CHECK // INITIALIZING", COLOR_CYAN_PRIMARY
        sub_surf = fonts["caption"].render(subtitle, True, subtitle_color)
        surface.blit(sub_surf, sub_surf.get_rect(center=(card_rect.centerx, card_y + 140)))

        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (card_x + 30, card_y + 160),
            (card_x + card_w - 30, card_y + 160),
            1,
        )

        # Diagnostic list: real status per subsystem, dotted leaders between.
        y_step = card_y + 178
        label_x = card_x + 84
        for step in self.steps:
            num_surf = fonts["mono"].render(f"[{step.step_id}]", True, COLOR_CYAN_PRIMARY)
            surface.blit(num_surf, (card_x + 40, y_step))

            lbl_surf = fonts["body"].render(step.label, True, COLOR_TEXT_WHITE)
            surface.blit(lbl_surf, (label_x, y_step))

            if step.is_failure:
                val_color = COLOR_ERROR
            elif step.completed:
                val_color = COLOR_ONLINE if step.status == "READY" else COLOR_STANDBY
            else:
                val_color = COLOR_TEXT_MUTED
            val_surf = fonts["mono"].render(step.status, True, val_color)
            val_rect = val_surf.get_rect(right=card_x + card_w - 40, top=y_step)

            dots_x = label_x + lbl_surf.get_width() + 8
            dots_w = val_rect.left - 8 - dots_x
            if dots_w > 8:
                dots_surf = fonts["mono"].render("." * (dots_w // 8), True, (35, 55, 80))
                surface.blit(dots_surf, (dots_x, y_step))

            surface.blit(val_surf, val_rect)
            y_step += 25

        # Progress bar and footer status.
        bar_y = card_y + card_h - 52
        bar_w = card_w - 80
        bar_h = 6
        bar_x = card_x + 40
        pygame.draw.rect(surface, (18, 30, 46), (bar_x, bar_y, bar_w, bar_h))

        fill_w = int(bar_w * self.progress_anim.progress)
        if fill_w > 0:
            fill_color = COLOR_ERROR if (self.camera_check_passed is False and self.progress_anim.progress > 0.8) else COLOR_CYAN_PRIMARY
            pygame.draw.rect(surface, fill_color, (bar_x, bar_y, fill_w, bar_h))

        pct_text = f"{int(self.progress_anim.progress * 100):3d}%"
        pct_surf = fonts["mono_small"].render(pct_text, True, COLOR_ICE_BLUE)
        surface.blit(pct_surf, pct_surf.get_rect(right=card_x + card_w - 40, bottom=bar_y - 4))

        if self.is_complete and not failure:
            msg, msg_col = f"{DISPLAY_VERSION.upper()} // INITIALIZING CAMERA HUD", COLOR_ONLINE
        elif failure and self.progress_anim.is_complete:
            msg, msg_col = "SUBSYSTEM UNAVAILABLE // TRANSITIONING TO ERROR RECOVERY", COLOR_ERROR
        else:
            msg, msg_col = "SUBSYSTEM VALIDATION IN PROGRESS...", COLOR_TEXT_MUTED

        stat_surf = fonts["mono_small"].render(msg, True, msg_col)
        surface.blit(stat_surf, (bar_x, bar_y + 12))
