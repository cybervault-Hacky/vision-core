"""Shutdown sequence for VisionCore.

Safety is not animated: the application releases the control layers, stops the
tracking engine and closes the camera *before* this screen is shown, and every
status line below reports the result of that cleanup. The sequence itself is
short, bounded and skippable (any key or click), so it can never hold up a
process that has already finished shutting down.
"""

from __future__ import annotations

import os
from typing import Dict, List, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from ui.animations import ProgressAnimation
from version import VERSION
from ui.theme import (
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_DISABLED,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_WARNING,
    draw_dot,
)

# Total duration of the sequence (the last step holds for the tail of it).
DURATION_SEC = 1.25

_STATUS_COLOR = {
    "RELEASED": COLOR_SUCCESS,
    "IDLE": COLOR_SUCCESS,
    "OFF": COLOR_SUCCESS,
    "DISABLED": COLOR_DISABLED,
    "STOPPED": COLOR_SUCCESS,
    "ERROR": COLOR_DANGER,
}


class ShutdownScreen:
    """Visualises the cleanup that has already happened."""

    def __init__(self, duration_sec: float = DURATION_SEC) -> None:
        self.duration = duration_sec
        self.progress = ProgressAnimation(duration=duration_sec)
        self.steps: List[Tuple[str, str, bool]] = []
        self.is_complete = False

    # -- sequence ---------------------------------------------------------- #

    def start(self, steps: Sequence[Tuple[str, str, bool]]) -> None:
        """Adopt the real cleanup results: ``(label, status, done)`` per step."""
        self.steps = list(steps)
        self.progress = ProgressAnimation(duration=self.duration)
        self.is_complete = False

    def update(self, dt: float) -> bool:
        """Advance the sequence; returns True once it has finished."""
        self.progress.update(dt)
        if self.progress.is_complete:
            self.is_complete = True
        return self.is_complete

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        screen_rect: pygame.Rect,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render one frame of the shutdown sequence."""
        surface.fill(COLOR_BG)

        column_width = min(420, screen_rect.width - 48)
        center_x = screen_rect.centerx
        rows = max(1, len(self.steps))
        y = screen_rect.centery - 40 - rows * 16

        title = fonts["display"].render("VisionCore", True, COLOR_TEXT)
        surface.blit(title, (center_x - title.get_width() // 2, y))
        y += title.get_height() + 6
        version = fonts["small"].render(f"v{VERSION}", True, COLOR_TEXT_FAINT)
        surface.blit(version, (center_x - version.get_width() // 2, y))
        y += version.get_height() + 26

        subtitle, color = (
            ("System idle", COLOR_SUCCESS)
            if self.is_complete
            else ("Shutting down", COLOR_TEXT_DIM)
        )
        line = fonts["caption"].render(subtitle, True, color)
        surface.blit(line, (center_x - line.get_width() // 2, y))
        y += line.get_height() + 20

        # Steps: dot, label, the real outcome of that cleanup step.
        for label, status, done in self.steps:
            row_center = y + 15
            status_color = _STATUS_COLOR.get(status, COLOR_WARNING if not done else COLOR_TEXT_DIM)
            draw_dot(surface, (center_x - column_width // 2 + 5, row_center),
                     status_color, 3)
            text = fonts["body"].render(label, True, COLOR_TEXT_DIM)
            surface.blit(text, (center_x - column_width // 2 + 20, row_center - text.get_height() // 2))
            value = fonts["mono_small"].render(status.capitalize(), True, status_color)
            surface.blit(
                value,
                (center_x + column_width // 2 - value.get_width(),
                 row_center - value.get_height() // 2),
            )
            y += 30
        y += 20

        # Thin progress bar.
        bar = pygame.Rect(center_x - column_width // 2, y, column_width, 3)
        pygame.draw.rect(surface, COLOR_BORDER, bar, border_radius=2)
        fill = int(bar.width * self.progress.progress)
        if fill > 2:
            pygame.draw.rect(
                surface,
                COLOR_SUCCESS if self.is_complete else COLOR_TEXT_DIM,
                (bar.left, bar.top, fill, bar.height), border_radius=2,
            )
        y += 3 + 16

        hint = fonts["small"].render(
            "Control released — camera and tracking stopped", True, COLOR_TEXT_FAINT
        )
        surface.blit(hint, (center_x - hint.get_width() // 2, y))
