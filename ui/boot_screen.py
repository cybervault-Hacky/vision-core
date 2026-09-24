"""VisionCore startup sequence.

The sequence is deliberately short and quiet: the wordmark, the version, one
line of real status per subsystem and a thin progress bar. Each step shows the
real result of the subsystem it names - the camera probe and the tracking engine
are asynchronous, so their steps stay at ``Checking``/``Loading`` until the
application reports what actually happened. The sequence never claims a
subsystem is ready before it is, ends on a real ``System ready`` or
``Recovery required`` result, and holds briefly so the transition into the
workspace reads as intentional.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from ui.animations import ProgressAnimation, PulseAnimation
from version import VERSION
from ui.theme import (
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DISABLED,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_WARNING,
    draw_dot,
)

# Short hold on "System ready" before the workspace takes over.
READY_HOLD_SEC = 0.55

# Sentence-case labels for the raw step statuses.
_STEP_LABELS = {
    "READY": ("Ready", COLOR_SUCCESS),
    "UNAVAILABLE": ("Unavailable", COLOR_DANGER),
    "DISABLED": ("Disabled", COLOR_DISABLED),
    "CHECKING...": ("Checking…", COLOR_WARNING),
    "LOADING...": ("Loading…", COLOR_WARNING),
    "PROBING...": ("Probing…", COLOR_WARNING),
    "INITIALIZING": ("Waiting…", COLOR_DISABLED),
}


def _step_label(status: str) -> Tuple[str, Tuple[int, int, int]]:
    return _STEP_LABELS.get(status, (status.capitalize(), COLOR_TEXT_DIM))


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
        self.pulse = PulseAnimation(min_val=0.3, max_val=1.0, frequency_hz=1.6)

        # Order matters: it is the order the interface genuinely comes up in.
        self.steps: List[BootStep] = [
            BootStep("01", "Camera", 0.18),
            BootStep("02", "Hand tracking", 0.42),
            BootStep("03", "Gesture engine", 0.62),
            BootStep("04", "Control layer", 0.78),
            BootStep("05", "Interface", 0.88),
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
                # The interface is up the moment it is drawn: a real result.
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

    def render(
        self,
        surface: pygame.Surface,
        screen_rect: pygame.Rect,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render the complete startup sequence frame."""
        surface.fill(COLOR_BG)

        column_width = min(420, screen_rect.width - 48)
        center_x = screen_rect.centerx
        y = screen_rect.centery - 150

        # Wordmark and version.
        title = fonts["display"].render("VisionCore", True, COLOR_TEXT)
        surface.blit(title, (center_x - title.get_width() // 2, y))
        y += title.get_height() + 6
        version = fonts["small"].render(f"v{VERSION}", True, COLOR_TEXT_FAINT)
        surface.blit(version, (center_x - version.get_width() // 2, y))
        y += version.get_height() + 34

        # One honest line about where the sequence is.
        failure = any(step.is_failure for step in self.steps)
        if self.is_complete and not failure:
            headline, color = "System ready", COLOR_SUCCESS
        elif failure and self.progress_anim.is_complete:
            headline, color = "Recovery required", COLOR_DANGER
        else:
            headline, color = "Starting up", COLOR_TEXT_DIM
        line = fonts["caption"].render(headline, True, color)
        surface.blit(line, (center_x - line.get_width() // 2, y))
        y += line.get_height() + 22

        # Subsystem checks: dot, label, real status.
        row_height = 30
        rows_top = y
        for step in self.steps:
            label, status_color = _step_label(step.status)
            row_center = y + row_height // 2
            dot_color = status_color
            if not step.completed and status_color is COLOR_WARNING:
                dot_color = tuple(
                    int(channel * (0.45 + 0.55 * self.pulse.value))
                    for channel in status_color
                )
            draw_dot(surface, (center_x - column_width // 2 + 5, row_center),
                     dot_color, 3)
            name = fonts["body"].render(step.label, True, COLOR_TEXT_DIM)
            surface.blit(name, (center_x - column_width // 2 + 20, row_center - name.get_height() // 2))
            status = fonts["mono_small"].render(label, True, status_color)
            surface.blit(
                status,
                (center_x + column_width // 2 - status.get_width(), row_center - status.get_height() // 2),
            )
            y += row_height
        y = max(y, rows_top + len(self.steps) * row_height) + 18

        # Thin progress bar.
        bar_width = column_width
        bar = pygame.Rect(center_x - bar_width // 2, y, bar_width, 3)
        pygame.draw.rect(surface, COLOR_BORDER, bar, border_radius=2)
        fill_width = int(bar_width * self.progress_anim.progress)
        if fill_width > 2:
            fill_color = COLOR_DANGER if failure else COLOR_SUCCESS
            pygame.draw.rect(
                surface, fill_color,
                (bar.left, bar.top, fill_width, bar.height), border_radius=2,
            )
        y += 3 + 14

        percent = f"{int(self.progress_anim.progress * 100)}%"
        if failure and self.progress_anim.is_complete:
            note = "A subsystem is unavailable — the interface will open in recovery"
        elif self.is_complete:
            note = "Opening the workspace"
        else:
            note = "Running locally — nothing leaves this machine"
        footer = fonts["small"].render(f"{note}   {percent}", True, COLOR_TEXT_FAINT)
        surface.blit(footer, (center_x - footer.get_width() // 2, y))
