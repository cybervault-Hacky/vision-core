"""Shared section-page chrome for the Controls and Settings workspaces.

Both pages are a centred column of grouped rows over a scrollable body, and
they must share one visual language down to the pixel - so the layout, the
section panels, the hairline-separated rows and the scrolling live here once.

A row is a plain dictionary built from live telemetry each frame:

``name`` / ``desc``
    The row's label and its short explanation.
``value``
    ``(text, colour)`` shown right-aligned in the mono face.
``buttons``
    A cluster of small buttons: ``(token, label, kind, icon, enabled)``.
``switch``
    ``(token, on, enabled)`` rendered as a toggle.
``segmented``
    ``(options, active_index, tokens)`` rendered as a segmented control.
``custom``
    A callable ``(surface, rect, telemetry, fonts)`` drawn in the row body.

Rows carry no behaviour: every interactive element records a hit rectangle
against a token, and the page resolves clicks into tokens for the window to
route to the existing application callbacks.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import pygame

from app.state import Telemetry
from ui import icons
from ui.theme import (
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_FAINT,
    draw_button,
    draw_panel,
    draw_scrollbar,
    draw_segmented,
    draw_switch,
    fit,
)

MAX_COLUMN_WIDTH = 840
PAGE_TITLE_HEIGHT = 62
SECTION_HEADER_HEIGHT = 46
SECTION_GAP = 16
SCROLL_STEP = 36


class SectionPage:
    """A scrollable page of grouped rows built from live telemetry."""

    title = ""

    def __init__(self) -> None:
        self.scroll = 0.0
        self._actions: Dict[str, pygame.Rect] = {}
        self._segment_hits: List[Tuple[str, pygame.Rect]] = []

    # -- events ------------------------------------------------------------ #

    def handle_wheel(self, dy: int) -> bool:
        """Scroll the page; ``dy`` follows pygame's wheel sign (up is positive)."""
        if not dy:
            return False
        self.scroll = max(0.0, self.scroll - dy * SCROLL_STEP)
        return True

    def handle_click(self, position: Tuple[int, int]) -> Optional[str]:
        for token, rect in self._segment_hits:
            if rect.collidepoint(position):
                return token
        for action, rect in self._actions.items():
            if rect.collidepoint(position):
                return action
        return None

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        self._actions = {}
        self._segment_hits = []

        column = self._column(rect)
        title = fonts["display"].render(self.title, True, COLOR_TEXT)
        surface.blit(title, (column.left, column.top + 8))

        body = pygame.Rect(
            column.left,
            column.top + PAGE_TITLE_HEIGHT,
            column.width,
            max(80, column.height - PAGE_TITLE_HEIGHT),
        )

        sections = self._build_sections(telemetry, fonts)
        content_height = sum(
            SECTION_HEADER_HEIGHT + sum(row["height"] for row in rows)
            for _title, _icon, rows in sections
        ) + SECTION_GAP * max(0, len(sections) - 1)
        max_scroll = max(0.0, content_height - body.height)
        self.scroll = min(self.scroll, max_scroll)

        previous_clip = surface.get_clip()
        surface.set_clip(body)
        try:
            y = body.top - self.scroll
            for title_text, icon_name, rows in sections:
                section_height = SECTION_HEADER_HEIGHT + sum(r["height"] for r in rows)
                if y + section_height > body.top and y < body.bottom:
                    self._draw_section(
                        surface,
                        pygame.Rect(body.left, y, body.width, section_height),
                        title_text, icon_name, rows, telemetry, fonts,
                    )
                y += section_height + SECTION_GAP
        finally:
            surface.set_clip(previous_clip)

        draw_scrollbar(surface, body, content_height, self.scroll)

    @staticmethod
    def _column(rect: pygame.Rect) -> pygame.Rect:
        width = min(MAX_COLUMN_WIDTH, rect.width - 32)
        return pygame.Rect(rect.centerx - width // 2, rect.top, width, rect.height)

    # -- section chrome ---------------------------------------------------- #

    def _draw_section(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        title: str,
        icon_name: str,
        rows: List[dict],
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        draw_panel(surface, rect, radius=14)

        mark = icons.icon(icon_name, 17, (98, 170, 220))
        text = fonts["heading"].render(title, True, COLOR_TEXT)
        surface.blit(mark, (rect.left + 18, rect.top + 15))
        surface.blit(text, (rect.left + 44, rect.top + 13))

        y = rect.top + SECTION_HEADER_HEIGHT
        for index, row in enumerate(rows):
            row_rect = pygame.Rect(rect.left + 8, y, rect.width - 16, row["height"])
            if row_rect.bottom > rect.top + SECTION_HEADER_HEIGHT:
                self._draw_row(surface, row_rect, row, telemetry, fonts)
            if index < len(rows) - 1:
                separator = row_rect.bottom
                if separator < rect.bottom:
                    pygame.draw.line(
                        surface, COLOR_BORDER,
                        (row_rect.left + 8, separator),
                        (row_rect.right - 8, separator),
                        1,
                    )
            y += row["height"]

    def _draw_row(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        row: dict,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        mouse = pygame.mouse.get_pos()
        name = fonts["body"].render(row["name"], True, COLOR_TEXT)
        surface.blit(name, (rect.left + 12, rect.top + 9))
        if row.get("desc"):
            desc = fonts["small"].render(
                fit(row["desc"], fonts["small"], rect.width - 150), True, COLOR_TEXT_FAINT
            )
            surface.blit(desc, (rect.left + 12, rect.top + 28))

        if row.get("segmented") is not None:
            options, active_index, tokens = row["segmented"]
            segmented = pygame.Rect(
                rect.right - 10 - min(200, max(150, len(options) * 84)),
                rect.centery - 16, min(200, max(150, len(options) * 84)), 32,
            )
            hovered_index = -1
            count = max(1, len(options))
            cell = segmented.width // count
            for index in range(len(options)):
                width = cell if index < count - 1 else segmented.width - cell * index
                hit = pygame.Rect(
                    segmented.left + index * cell, segmented.top, width, segmented.height,
                )
                if hit.collidepoint(mouse):
                    hovered_index = index
            hits = draw_segmented(
                surface, segmented, options, active_index, fonts, hovered_index
            )
            for index, hit in enumerate(hits):
                if index < len(tokens):
                    self._segment_hits.append((tokens[index], hit))
            return

        if row.get("switch") is not None:
            token, on, enabled = row["switch"]
            switch_rect = pygame.Rect(rect.right - 52, rect.centery - 13, 44, 26)
            draw_switch(
                surface, switch_rect, on,
                hovered=switch_rect.collidepoint(mouse), enabled=enabled,
            )
            self._actions[token] = switch_rect
            return

        buttons: Sequence[Tuple[str, str, str, Optional[str], bool]] = row.get("buttons", ())
        if buttons:
            x = rect.right - 10
            for token, label, kind, icon_name, enabled in reversed(buttons):
                width = row.get("button_width", 96)
                button_rect = pygame.Rect(x - width, rect.centery - 15, width, 30)
                draw_button(
                    surface, button_rect, label, fonts, kind=kind, enabled=enabled,
                    hovered=button_rect.collidepoint(mouse) and enabled,
                    icon=icon_name, icon_module=icons,
                )
                self._actions[token] = button_rect
                x -= width + 8
            return

        if row.get("custom") is not None:
            row["custom"](surface, rect, telemetry, fonts)
            return

        value = row.get("value")
        if value is None:
            return
        text, color = value
        value_surf = fonts["mono"].render(text, True, color)
        surface.blit(
            value_surf,
            (rect.right - 12 - value_surf.get_width(), rect.centery - 2),
        )

    # -- subclass hook ----------------------------------------------------- #

    def _build_sections(
        self, telemetry: Telemetry, fonts: Dict[str, pygame.font.Font]
    ) -> List[Tuple[str, str, List[dict]]]:
        raise NotImplementedError
