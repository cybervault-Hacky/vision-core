"""Action feedback: notification, error strip and the recent-action timeline.

Two reusable views built from the same real events:

* :meth:`FeedbackView.render_overlay` draws the notification for the action that
  just executed (LEFT CLICK, DRAG START, SCROLL DOWN, VOLUME UP, NEXT TRACK,
  WINDOW SWITCH, APP LAUNCHED, ...) plus the error strip for an error state.
  Both come from the interaction director, so a notification is only ever shown
  for an action the backend accepted, and a RETRY button only exists when the
  application really can retry.
* :meth:`FeedbackView.render_timeline_panel` draws the recent-action timeline
  for the sidebar: newest first, capped at a handful of rows, held in memory
  only and dropped when the application exits.

The notification is a compact card at the top of the viewport: it animates in,
holds, fades out on its own timeline and never blocks the video. Cards are built
once per distinct label and reused, so a frame costs one blit.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import pygame

from app.interaction import ActionFeedback, FeedbackSource, RecoveryAction
from app.interaction.feedback import FEEDBACK_DURATION, TIMELINE_LIMIT
from app.state import Telemetry
from ui.hud import (
    COLOR_CYAN_PRIMARY,
    COLOR_DISABLED,
    COLOR_ERROR,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BG,
    COLOR_STANDBY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)

# Notification geometry.
TOAST_PADDING = 10
TOAST_MIN_WIDTH = 168
TOAST_MAX_WIDTH = 360
TOAST_TOP_OFFSET = 46
TOAST_GAP = 6

# Error strip geometry.
ERROR_PADDING = 10
ERROR_BUTTON_SIZE = (74, 22)

# Timeline panel.
ROW_STEP = 15
ROW_LIMIT = 7

_SOURCE_COLOR: Dict[FeedbackSource, Tuple[int, int, int]] = {
    FeedbackSource.MOUSE: COLOR_ONLINE,
    FeedbackSource.DEVICE: COLOR_ICE_BLUE,
    FeedbackSource.MODE: COLOR_CYAN_PRIMARY,
    FeedbackSource.SAFETY: COLOR_STANDBY,
    FeedbackSource.SYSTEM: COLOR_TEXT_MUTED,
}


class FeedbackView:
    """Renders the notification, the error strip and the recent-action timeline."""

    def __init__(self, hud) -> None:
        # The HUD manager owns the shared panel chrome, so notifications and
        # sidebar modules cannot drift apart visually.
        self._hud = hud
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
        window can route the click; otherwise ``None``.
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
                    # Draining underline: the notification's own lifetime, drawn
                    # on the viewport instead of copying the card every frame.
                    width = max(1, int((card.get_width() - 2) * alpha))
                    pygame.draw.rect(
                        surface,
                        (*COLOR_ICE_BLUE, 120),
                        (rect.left + 1, rect.bottom - 3, width, 2),
                    )
        return retry

    # -- notification card ------------------------------------------------- #

    def _toast_card(
        self,
        entry: ActionFeedback,
        fonts: Dict[str, pygame.font.Font],
    ) -> pygame.Surface:
        """Notification card for one event, built once and reused."""
        color = (
            COLOR_ERROR
            if not entry.success
            else _SOURCE_COLOR.get(entry.source, COLOR_ICE_BLUE)
        )
        key = (entry.display_label, entry.detail, color, entry.success)
        card = self._toast_cache.get(key)
        if card is not None:
            return card

        label = fonts["body"].render(entry.display_label, True, COLOR_TEXT_WHITE)
        source = fonts["mono_small"].render(entry.source.label, True, color)
        detail = (
            fonts["mono_small"].render(entry.detail, True, COLOR_TEXT_MUTED)
            if entry.detail
            else None
        )
        text_width = max(
            label.get_width() + source.get_width() + 18,
            detail.get_width() if detail is not None else 0,
        )
        width = max(TOAST_MIN_WIDTH, min(TOAST_MAX_WIDTH, text_width + TOAST_PADDING * 2))
        height = TOAST_PADDING * 2 + label.get_height()
        if detail is not None:
            height += detail.get_height() + 2

        card = pygame.Surface((width, height), pygame.SRCALPHA)
        card.fill((*COLOR_PANEL_BG, 224))
        pygame.draw.rect(card, (*color, 190), card.get_rect(), 1)
        # The left edge carries the outcome colour: also red for a refusal.
        pygame.draw.rect(card, color, (1, 1, 3, height - 2))
        card.blit(label, (TOAST_PADDING, TOAST_PADDING - 1))
        card.blit(
            source,
            source.get_rect(top=TOAST_PADDING + 1, right=width - TOAST_PADDING),
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
        max_width = max(180, min(TOAST_MAX_WIDTH + 90, viewport.width - 24))
        title = fonts["caption"].render(error.title, True, COLOR_ERROR)
        detail_text = self._fit(error.detail or "", fonts["mono_small"], max_width - ERROR_PADDING * 2)
        detail = fonts["mono_small"].render(detail_text, True, COLOR_TEXT_MUTED)

        width = max(title.get_width(), detail.get_width()) + ERROR_PADDING * 2
        if error.recovery is not None:
            width += ERROR_BUTTON_SIZE[0] + ERROR_PADDING
        width = min(width, max_width)

        height = ERROR_PADDING * 2 + title.get_height()
        if detail_text:
            height += detail.get_height() + 2

        card = pygame.Surface((width, height), pygame.SRCALPHA)
        card.fill((*COLOR_PANEL_BG, 232))
        pygame.draw.rect(card, (*COLOR_ERROR, 200), card.get_rect(), 1)
        # A short red rule marks the card as an error rather than a status line.
        pygame.draw.rect(card, COLOR_ERROR, (1, 1, 3, height - 2))

        card.blit(title, (ERROR_PADDING, ERROR_PADDING - 1))
        if detail_text:
            card.blit(detail, (ERROR_PADDING, ERROR_PADDING + title.get_height()))

        retry_rect: Optional[pygame.Rect] = None
        if error.recovery is not None:
            button = pygame.Rect(0, 0, *ERROR_BUTTON_SIZE)
            button.midright = (width - ERROR_PADDING, height // 2)
            hovered = button.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(card, (14, 44, 36) if hovered else (12, 30, 26), button)
            pygame.draw.rect(card, COLOR_ONLINE, button, 1)
            label = fonts["mono_small"].render(
                error.recovery_label or "RETRY", True, COLOR_ONLINE
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
        while shortened and font.size(shortened + "...")[0] > max_width:
            shortened = shortened[:-1]
        return (shortened + "...") if shortened else ""

    # -- timeline panel ---------------------------------------------------- #

    def render_timeline_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Sidebar module: the most recent real actions, newest first."""
        self._hud.draw_chamfer_panel(surface, rect)
        self._hud.draw_panel_header(surface, rect, f"RECENT ACTIONS ({TIMELINE_LIMIT})", fonts)

        rows = telemetry.feedback[:ROW_LIMIT]
        if not rows:
            empty = fonts["mono_small"].render("NO ACTIONS YET", True, COLOR_DISABLED)
            surface.blit(empty, (rect.left + 16, rect.top + 44))
            return

        now = telemetry.interaction.now
        y = rect.top + 40
        for entry in rows:
            if y + 11 > rect.bottom - 6:
                break
            fresh = entry.age(now) < FEEDBACK_DURATION
            color = (
                COLOR_ERROR
                if not entry.success
                else (_SOURCE_COLOR.get(entry.source, COLOR_ICE_BLUE) if fresh else COLOR_DISABLED)
            )
            stamp = fonts["mono_small"].render(entry.clock_label, True, color)
            surface.blit(stamp, (rect.left + 16, y))

            available = rect.right - 16 - (rect.left + 16 + stamp.get_width() + 10)
            label = self._fit_label(entry.display_label, fonts["mono_small"], available)
            text = fonts["mono_small"].render(
                label, True, COLOR_TEXT_WHITE if fresh else COLOR_TEXT_MUTED
            )
            surface.blit(text, (rect.right - 16 - text.get_width(), y))
            y += ROW_STEP

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
