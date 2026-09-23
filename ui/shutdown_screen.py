"""Shutdown sequence for VisionCore.

Safety is not animated: the application releases the control layers, stops the
tracking engine and closes the camera *before* this screen is shown, and every
status line below reports the result of that cleanup. The sequence itself is
short, bounded and skippable (any key or click), so it can never hold up a
process that has already finished shutting down.
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from ui.animations import ProgressAnimation, PulseAnimation
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

# Total duration of the sequence (the last step holds for the tail of it).
DURATION_SEC = 1.25


class ShutdownScreen:
    """Visualises the cleanup that has already happened."""

    def __init__(self, duration_sec: float = DURATION_SEC) -> None:
        self.duration = duration_sec
        self.progress = ProgressAnimation(duration=duration_sec)
        self.pulse = PulseAnimation(min_val=0.35, max_val=1.0, frequency_hz=1.4)
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
        self.pulse.update(dt)
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
        surface.fill(COLOR_BG_DARK)

        card_w = min(560, screen_rect.width - 40)
        rows = max(1, len(self.steps))
        card_h = 128 + rows * 24
        card = pygame.Rect(0, 0, card_w, card_h)
        card.center = screen_rect.center

        pygame.draw.rect(surface, COLOR_PANEL_BG, card)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, card, 1)

        # Corner brackets: the same restrained frame as the other screens.
        length = 18
        for x, y, dx, dy in (
            (card.left, card.top, 1, 1),
            (card.right, card.top, -1, 1),
            (card.left, card.bottom, 1, -1),
            (card.right, card.bottom, -1, -1),
        ):
            pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y), (x + length * dx, y), 2)
            pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y), (x, y + length * dy), 2)

        title = fonts["title"].render("VISIONCORE", True, COLOR_TEXT_WHITE)
        surface.blit(title, title.get_rect(center=(card.centerx, card.top + 34)))

        subtitle, color = (
            ("SYSTEM IDLE", COLOR_ONLINE)
            if self.is_complete
            else ("SHUTTING DOWN", COLOR_CYAN_PRIMARY)
        )
        sub = fonts["caption"].render(subtitle, True, color)
        surface.blit(sub, sub.get_rect(center=(card.centerx, card.top + 60)))

        # Steps: the label plus the real outcome of that cleanup step.
        y = card.top + 88
        for label, status, done in self.steps:
            text = fonts["mono"].render(label, True, COLOR_TEXT_WHITE)
            surface.blit(text, (card.left + 34, y))

            if done:
                status_color = COLOR_ONLINE
            elif status in ("ERROR", "UNAVAILABLE"):
                status_color = COLOR_ERROR
            else:
                status_color = COLOR_STANDBY
            value = fonts["mono_small"].render(status, True, status_color)
            surface.blit(value, value.get_rect(right=card.right - 34, top=y + 2))

            dots_x = card.left + 34 + text.get_width() + 8
            dots_w = (card.right - 34 - value.get_width() - 8) - dots_x
            if dots_w > 8:
                dots = fonts["mono_small"].render("." * (dots_w // 8), True, (35, 55, 80))
                surface.blit(dots, (dots_x, y + 2))
            y += 24

        # Progress bar and a pulsing marker while the sequence is running.
        bar = pygame.Rect(card.left + 34, card.bottom - 30, card.width - 68, 5)
        pygame.draw.rect(surface, (18, 30, 46), bar)
        fill = int(bar.width * self.progress.progress)
        if fill > 0:
            pygame.draw.rect(
                surface,
                COLOR_ONLINE if self.is_complete else COLOR_CYAN_PRIMARY,
                (bar.left, bar.top, fill, bar.height),
            )
        if not self.is_complete:
            marker = bar.left + fill
            pulse = 0.5 + 0.5 * math.sin(self.pulse.value * math.tau)
            pygame.draw.circle(
                surface,
                tuple(int(channel * (0.4 + 0.6 * pulse)) for channel in COLOR_ICE_BLUE),
                (marker, bar.centery),
                4,
            )

        hint = fonts["mono_small"].render(
            "CONTROL RELEASED - CAMERA AND TRACKING STOPPED", True, COLOR_TEXT_MUTED
        )
        surface.blit(hint, hint.get_rect(center=(card.centerx, card.bottom - 12)))
