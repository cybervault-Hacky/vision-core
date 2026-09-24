"""Action feedback: the notification toast, the error strip and the activity rows.

Everything shown here is produced by the interaction director from real action
results, so a notification only ever appears for an action a backend accepted,
and a RETRY button only exists when the application really can retry.

* :meth:`FeedbackView.render_overlay` draws the transient notification and the
  error strip over the camera viewport;
* :meth:`FeedbackView.render_activity` draws the recent-action rows for the
  Controls workspace, newest first, capped at a handful of rows, held in memory
  only and dropped when the application exits.

Cards are built once per distinct label and reused, so a frame costs one blit.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import pygame

from app.interaction import ActionFeedback, FeedbackSource, RecoveryAction
from app.interaction.feedback import FEEDBACK_DURATION
from app.state import Telemetry
from ui.theme import (
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_DISABLED,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_WARNING,
)

# Notification geometry.
TOAST_PADDING = 12
TOAST_MIN_WIDTH = 180
TOAST_MAX_WIDTH = 380
TOAST_TOP_OFFSET = 18
TOAST_GAP = 8

# Error strip geometry.
ERROR_PADDING = 12
ERROR_BUTTON_SIZE = (86, 26)

# Activity rows.
ROW_STEP = 22
ROW_LIMIT = 8

_SOURCE_COLOR: Dict[FeedbackSource, Tuple[int, int, int]] = {
    FeedbackSource.MOUSE: COLOR_SUCCESS,
    FeedbackSource.DEVICE: COLOR_ACCENT,
    FeedbackSource.MODE: COLOR_ACCENT,
    FeedbackSource.SAFETY: COLOR_WARNING,
    FeedbackSource.SYSTEM: COLOR_TEXT_DIM,
}


class FeedbackView:
    """Renders the notification, the error strip and the activity rows."""

    def __init__(self) -> None:
        self._toast_cache: Dict[Tuple[str, str, tuple, bool], pygame.Surface] = {}

    # -- overlay (error strip + notification) ------------------------------ #

    def render_overlay(
        self,
        surface: pygame.Surface,
        viewport: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> Optional[Tuple[RecoveryAction, pygame.Rect]]:
        """Draw the error strip and the notification at the top of the viewport.

        Returns the RETRY button (with the recovery it triggers) when an error
        state offers a recovery the application can actually perform, so the
        page can route the click; otherwise ``None``.
        """
        interaction = telemetry.interaction
        top = viewport.top + TOAST_TOP_OFFSET
        retry: Optional[Tuple[RecoveryAction, pygame.Rect]] = None

        # Errors outrank notifications: an error describes a condition that is
        # still true, a notification describes something that already ended.
        if interaction.error is not None:
            strip, target_rect = self._error_strip(viewport, interaction.error, fonts)
            strip_rect = strip.get_rect(midtop=(viewport.centerx, top))
            surface.blit(strip, strip_rect)
            if target_rect is not None and interaction.error.recovery is not None:
                retry = (interaction.error.recovery, target_rect.move(strip_rect.topleft))
            top = strip_rect.bottom + TOAST_GAP

        toast = telemetry.toast
        if toast is not None:
            duration = 1.0e9 if toast.sticky else FEEDBACK_DURATION
            alpha = toast.alpha(interaction.now, duration)
            if alpha > 0.02:
                card = self._toast_card(toast, fonts)
                rect = card.get_rect(midtop=(viewport.centerx, top))
                if rect.bottom <= viewport.bottom - 8:
                    card.set_alpha(int(255 * alpha))
                    surface.blit(card, rect)
        return retry

    # -- notification card ------------------------------------------------- #

    def _toast_card(
        self,
        entry: ActionFeedback,
        fonts: Dict[str, pygame.font.Font],
    ) -> pygame.Surface:
        """Notification card for one event, built once and reused."""
        color = (
            COLOR_DANGER
            if not entry.success
            else _SOURCE_COLOR.get(entry.source, COLOR_ACCENT)
        )
        key = (entry.display_label, entry.detail, color, entry.success)
        card = self._toast_cache.get(key)
        if card is not None:
            return card

        label = fonts["body"].render(entry.display_label, True, COLOR_TEXT)
        source = fonts["small"].render(entry.source.label, True, color)
        detail = (
            fonts["small"].render(entry.detail, True, COLOR_TEXT_DIM)
            if entry.detail
            else None
        )
        text_width = max(
            label.get_width() + source.get_width() + 22,
            detail.get_width() if detail is not None else 0,
        )
        width = max(TOAST_MIN_WIDTH, min(TOAST_MAX_WIDTH, text_width + TOAST_PADDING * 2))
        height = TOAST_PADDING * 2 + label.get_height()
        if detail is not None:
            height += detail.get_height() + 2

        card = pygame.Surface((width, height), pygame.SRCALPHA)
        card.fill((12, 14, 17, 216))
        pygame.draw.rect(card, (46, 52, 60, 230), card.get_rect(), 1, border_radius=10)
        # The left edge carries the outcome colour: also red for a refusal.
        pygame.draw.rect(card, (*color, 230), (1, 8, 3, height - 16), border_radius=2)
        card.blit(label, (TOAST_PADDING, TOAST_PADDING - 1))
        card.blit(
            source,
            source.get_rect(top=TOAST_PADDING + 2, right=width - TOAST_PADDING),
        )
        if detail is not None:
            card.blit(detail, (TOAST_PADDING, TOAST_PADDING + label.get_height()))

        self._toast_cache[key] = card
        if len(self._toast_cache) > 48:
            self._toast_cache.clear()
        return card

    # -- error strip ------------------------------------------------------- #

    def _error_strip(
        self,
        viewport: pygame.Rect,
        error,
        fonts: Dict[str, pygame.font.Font],
    ) -> Tuple[pygame.Surface, Optional[pygame.Rect]]:
        """Compact error card: readable title, real detail, optional RETRY."""
        max_width = max(200, min(TOAST_MAX_WIDTH + 110, viewport.width - 24))
        title = fonts["caption"].render(error.title, True, COLOR_DANGER)
        detail_text = self._fit(error.detail or "", fonts["small"], max_width - ERROR_PADDING * 2)
        detail = fonts["small"].render(detail_text, True, COLOR_TEXT_DIM) if detail_text else None

        width = max(title.get_width(), detail.get_width() if detail else 0) + ERROR_PADDING * 2
        if error.recovery is not None:
            width += ERROR_BUTTON_SIZE[0] + ERROR_PADDING
        width = min(width, max_width)

        height = ERROR_PADDING * 2 + title.get_height()
        if detail is not None:
            height += detail.get_height() + 2

        card = pygame.Surface((width, height), pygame.SRCALPHA)
        card.fill((34, 20, 21, 232))
        pygame.draw.rect(card, (*COLOR_DANGER, 190), card.get_rect(), 1, border_radius=10)
        pygame.draw.rect(card, (*COLOR_DANGER, 230), (1, 8, 3, height - 16), border_radius=2)

        card.blit(title, (ERROR_PADDING, ERROR_PADDING - 1))
        if detail is not None:
            card.blit(detail, (ERROR_PADDING, ERROR_PADDING + title.get_height()))

        retry_rect: Optional[pygame.Rect] = None
        if error.recovery is not None:
            button = pygame.Rect(0, 0, *ERROR_BUTTON_SIZE)
            button.midright = (width - ERROR_PADDING, height // 2)
            hovered = button.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(card, (56, 30, 32) if hovered else (46, 26, 28), button,
                             border_radius=7)
            pygame.draw.rect(card, COLOR_DANGER, button, 1, border_radius=7)
            label = fonts["small"].render(
                error.recovery_label or "Retry", True, COLOR_DANGER
            )
            card.blit(label, label.get_rect(center=button.center))
            retry_rect = button
        return card, retry_rect

    @staticmethod
    def _fit(text: str, font: pygame.font.Font, max_width: int) -> str:
        """Shorten text until it fits, so a card never spills outside itself."""
        if not text or font.size(text)[0] <= max_width:
            return text
        shortened = text
        while shortened and font.size(shortened + "…")[0] > max_width:
            shortened = shortened[:-1]
        return (shortened + "…") if shortened else ""

    # -- activity rows (Controls workspace) -------------------------------- #

    def render_activity(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> int:
        """Recent real actions, newest first. Returns the height used."""
        rows = telemetry.feedback[:ROW_LIMIT]
        if not rows:
            text = fonts["caption"].render("No actions yet", True, COLOR_DISABLED)
            surface.blit(text, (rect.left + 4, rect.top))
            return text.get_height()

        now = telemetry.interaction.now
        y = rect.top
        for entry in rows:
            if y + 12 > rect.bottom:
                break
            fresh = entry.age(now) < FEEDBACK_DURATION
            color = (
                COLOR_DANGER
                if not entry.success
                else (_SOURCE_COLOR.get(entry.source, COLOR_ACCENT) if fresh else COLOR_DISABLED)
            )
            pygame.draw.circle(surface, color, (rect.left + 4, y + 8), 3)
            stamp = fonts["mono_small"].render(entry.clock_label, True, COLOR_DISABLED)
            surface.blit(stamp, (rect.left + 16, y))

            available = rect.right - (rect.left + 16 + stamp.get_width() + 10)
            label = self._fit_label(entry.display_label, fonts["caption"], available)
            text = fonts["caption"].render(
                label, True, COLOR_TEXT if fresh else COLOR_TEXT_DIM
            )
            surface.blit(text, (rect.right - text.get_width(), y))
            y += ROW_STEP
        return y - rect.top

    @staticmethod
    def _fit_label(label: str, font: pygame.font.Font, max_width: int) -> str:
        """Trim a label to the space left of the stamp (keeps the count suffix)."""
        if font.size(label)[0] <= max_width:
            return label
        suffix = ""
        if "  x" in label:
            label, _, count = label.partition("  x")
            suffix = f" x{count}"
        while label and font.size(label + suffix)[0] > max_width:
            label = label[:-1]
        return (label + suffix) if label else suffix.strip()
