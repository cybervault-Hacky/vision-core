"""The VisionCore AI workspace: a dedicated conversational surface.

An original interface with the usability of a modern chat application - a clean
conversation column, a persistent composer, a helpful empty state - rendered in
VisionCore's own design language. Three rules shape it:

* the assistant's real status is always visible (Ready, Not configured,
  Processing, Error), never an animation standing in for work that is not
  happening, and a reply is labelled with the source it really came from;
* the page owns no application state beyond the text being typed: everything it
  shows comes from the published :class:`~app.ai.types.AISnapshot` and
  :class:`~app.voice.types.VoiceSnapshot`;
* every action - send, microphone, confirm, cancel - is raised as an intent for
  the window to route to the existing application pathways. There is no second
  AI implementation here.
"""

from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.ai.types import ChatMessage, ChatRole, ResponseSource
from app.state import Telemetry
from app.voice.types import VoiceSnapshot, VoiceState
from ui import icons
from ui.theme import (
    COLOR_ACCENT,
    COLOR_ACCENT_STRONG,
    COLOR_BORDER,
    COLOR_BORDER_STRONG,
    COLOR_DANGER,
    COLOR_DISABLED,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_WARNING,
    COLOR_WARNING_TINT,
    ai_status_color,
    voice_state_color,
    draw_button,
    draw_scrollbar,
    ease_out_cubic,
    fit,
    wrap,
)

# Geometry.
MAX_COLUMN_WIDTH = 760
HEADER_HEIGHT = 58
COMPOSER_MIN_HEIGHT = 52
COMPOSER_MAX_LINES = 3
GAP = 14
MAX_BLOCKS = 40          # bounded rendering: the conversation is memory-only
MAX_INPUT_CHARS = 600
CARET_PERIOD = 1.1

# Conversation pacing.
LINE_GAP = 4
MESSAGE_GAP = 16
APPEAR_DURATION = 0.20

# Microphone level meter (measured amplitude only).
LEVEL_SEGMENTS = 14
LEVEL_ATTACK = 0.55
LEVEL_RELEASE = 0.10

# Suggestions offered in the empty state: every one is a real question the
# existing assistant pathway answers (locally when no provider is configured).
SUGGESTIONS = (
    "What can VisionCore do?",
    "What's the tracking state?",
    "How do the gestures work?",
    "What am I doing right now?",
)

# Accent for a system result line, keyed by its detail tag.
_NOTE_ACCENT = {
    "RESULT": COLOR_TEXT_DIM,
    "CONFIRM": COLOR_WARNING,
    "EXPIRED": COLOR_WARNING,
    "SUPERSEDED": COLOR_WARNING,
    "REFUSED": COLOR_DANGER,
    "NO ROUTE": COLOR_DANGER,
}


class AIPage:
    """The AI workspace: conversation, composer and their intents."""

    def __init__(self) -> None:
        self.input_text = ""
        self.scroll = 0.0
        self._actions: Dict[str, pygame.Rect] = {}
        self._conversation_rect = pygame.Rect(0, 0, 0, 0)
        self._send_rect = pygame.Rect(0, 0, 0, 0)
        self._microphone_rect = pygame.Rect(0, 0, 0, 0)
        self._level_shown = 0.0
        self._last_message_key: Optional[tuple] = None
        self._appear_started = 0.0
        self._wrap_cache: Dict[tuple, tuple] = {}
        self._voice: VoiceSnapshot = VoiceSnapshot()

    # -- state ------------------------------------------------------------- #

    def clear_input(self) -> None:
        self.input_text = ""
        self._wrap_cache.clear()

    def consume_input(self) -> str:
        """Take the typed message (the caller clears the field by consuming it)."""
        text = self.input_text.strip()
        self.input_text = ""
        self._wrap_cache.clear()
        return text

    # -- events ------------------------------------------------------------ #

    def handle_key(self, event: pygame.event.Event) -> Optional[str]:
        """Type into the composer. Returns a page intent when one is raised."""
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if event.mod & pygame.KMOD_SHIFT:
                # Shift + Enter inserts a newline; Enter alone sends.
                if len(self.input_text) < MAX_INPUT_CHARS:
                    self.input_text += "\n"
                return None
            return "send"
        if event.key == pygame.K_BACKSPACE:
            self.input_text = self.input_text[:-1]
        elif event.key == pygame.K_DELETE:
            self.clear_input()
        elif event.key == pygame.K_UP:
            self.scroll += 28
        elif event.key == pygame.K_DOWN:
            self.scroll = max(0.0, self.scroll - 28)
        else:
            character = getattr(event, "unicode", "")
            if character and character.isprintable() and len(self.input_text) < MAX_INPUT_CHARS:
                self.input_text += character
        return None

    def handle_wheel(self, dy: int) -> bool:
        """Scroll the conversation when the pointer is over it."""
        if not dy or not self._conversation_rect.collidepoint(pygame.mouse.get_pos()):
            return False
        self.scroll = max(0.0, self.scroll + dy * 32)
        return True

    def handle_click(self, position: Tuple[int, int]) -> Optional[str]:
        """Route a click inside the page to a page intent."""
        for action, rect in self._actions.items():
            if rect.collidepoint(position):
                return action
        return None

    # -- layout ------------------------------------------------------------ #

    @staticmethod
    def _column(rect: pygame.Rect) -> pygame.Rect:
        """The conversation column: comfortable width, centred."""
        width = min(MAX_COLUMN_WIDTH, rect.width - 32)
        return pygame.Rect(
            rect.centerx - width // 2, rect.top, width, rect.height
        )

    def _composer_height(self, fonts: Dict[str, pygame.font.Font]) -> int:
        lines = self._input_lines(fonts, 10 ** 6)[0]
        line_height = fonts["body"].get_linesize() + 2
        body = COMPOSER_MIN_HEIGHT - 18 + min(COMPOSER_MAX_LINES, lines) * line_height
        return max(COMPOSER_MIN_HEIGHT, body + 18)

    def _input_lines(
        self, fonts: Dict[str, pygame.font.Font], width: int
    ) -> Tuple[int, List[str]]:
        """Wrap the composer text; returns (line count, visible lines)."""
        if not self.input_text:
            return 1, [""]
        lines = wrap(self.input_text, fonts["body"], max(40, width))
        shown = lines[-COMPOSER_MAX_LINES:]
        if len(lines) > COMPOSER_MAX_LINES:
            shown = ["…" + shown[0][1:] if shown[0] else "…"] + shown[1:]
        return len(lines), shown

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        now: Optional[float] = None,
    ) -> None:
        """Draw the workspace from the assistant and voice snapshots."""
        now = time.perf_counter() if now is None else now
        snapshot = telemetry.ai
        self._voice = telemetry.voice
        self._actions = {}

        column = self._column(rect)

        composer_height = self._composer_height(fonts)
        voice_height = self._voice_line_height()
        confirm_height = 48 if snapshot.pending is not None else 0
        bottom_stack = composer_height + voice_height + confirm_height + 12

        header_rect = pygame.Rect(column.left, column.top, column.width, HEADER_HEIGHT)
        conversation = pygame.Rect(
            column.left,
            header_rect.bottom + 4,
            column.width,
            max(60, column.bottom - bottom_stack - header_rect.bottom - 4),
        )
        self._conversation_rect = conversation

        self._draw_header(surface, header_rect, snapshot, telemetry, fonts)
        self._draw_conversation(surface, conversation, snapshot, fonts, now)

        y = column.bottom - composer_height
        if snapshot.pending is not None:
            self._draw_confirmation(
                surface,
                pygame.Rect(column.left, y - confirm_height - 6, column.width, confirm_height),
                snapshot,
                fonts,
            )
            y -= confirm_height + 6
        if voice_height:
            self._draw_voice_line(
                surface,
                pygame.Rect(column.left, y - voice_height, column.width, voice_height),
                fonts,
            )
            y -= voice_height
        self._draw_composer(
            surface, pygame.Rect(column.left, y, column.width, composer_height),
            snapshot, fonts, now,
        )

    # -- header ------------------------------------------------------------ #

    def _draw_header(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        snapshot,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        color = ai_status_color(snapshot.status)
        pygame.draw.circle(surface, color, (rect.left + 5, rect.top + 10), 4)
        status = fonts["heading"].render(snapshot.status_label, True, COLOR_TEXT)
        surface.blit(status, (rect.left + 16, rect.top - 2))

        provider = snapshot.header_note if snapshot.configured else "Local answers"
        provider_surf = fonts["small"].render(provider, True, COLOR_TEXT_FAINT)
        surface.blit(
            provider_surf,
            (max(rect.left + 16, rect.right - provider_surf.get_width()), rect.top + 1),
        )

        # Secondary line: a real failure in danger, otherwise the live context.
        if snapshot.error is not None:
            detail = snapshot.error_detail or ""
            line = f"{snapshot.error.label}" + (f" — {detail}" if detail else "")
            text = fonts["small"].render(fit(line, fonts["small"], rect.width - 16), True,
                                         COLOR_DANGER)
            surface.blit(text, (rect.left, rect.top + 24))
            return

        context = self._context_line(telemetry)
        text = fonts["small"].render(
            fit(context, fonts["small"], rect.width), True, COLOR_TEXT_FAINT
        )
        surface.blit(text, (rect.left, rect.top + 24))

    @staticmethod
    def _context_line(telemetry: Telemetry) -> str:
        """One line of real state: only what actually exists in runtime state."""
        camera = (
            f"Camera {telemetry.camera_width}×{telemetry.camera_height}"
            if telemetry.camera_width > 0
            else "Camera unavailable"
        )
        tracking = AIPage._tracking_word(telemetry)
        mode = f"{telemetry.control_mode.label.lower()} mode"
        gesture = (
            telemetry.gesture.label.lower()
            if telemetry.gesture is not None
            else "none"
        )
        parts = [camera, tracking, mode, f"gesture {gesture}"]
        if telemetry.voice.state.value not in ("OFF", ""):
            parts.append(f"voice {telemetry.voice.status_label.lower()}")
        return " · ".join(parts)

    @staticmethod
    def _tracking_word(telemetry: Telemetry) -> str:
        from app.state import SubsystemState

        if telemetry.tracking in (SubsystemState.DISABLED,):
            return "tracking off"
        if telemetry.tracking in (SubsystemState.UNAVAILABLE, SubsystemState.ERROR):
            return "tracking unavailable"
        return f"tracking {telemetry.tracking_state.status_label.lower()}"

    # -- conversation ------------------------------------------------------ #

    def _draw_conversation(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
        now: float,
    ) -> None:
        """Draw the conversation, pinned to the newest entry."""
        messages: Sequence[ChatMessage] = tuple(snapshot.messages)[-MAX_BLOCKS:]

        # Track the newest message so it can ease in on appearance.
        key = (len(messages), messages[-1].timestamp if messages else 0.0,
               len(messages[-1].content) if messages else 0)
        if key != self._last_message_key:
            self._last_message_key = key
            self._appear_started = now
            self.scroll = 0.0

        blocks = self._measure(messages, fonts, area.width)
        content_height = sum(height for _, height in blocks)
        if not blocks:
            content_height = 20
        if snapshot.busy:
            content_height += 26

        max_scroll = max(0.0, content_height - area.height)
        self.scroll = min(self.scroll, max_scroll)

        if not blocks and not snapshot.busy:
            self._draw_empty_state(surface, area, fonts)
            return

        previous_clip = surface.get_clip()
        surface.set_clip(area)
        try:
            # Short conversations read from the top; once the history is taller
            # than the area it stays pinned to the newest entry at the bottom.
            if content_height > area.height:
                y = area.bottom - content_height + self.scroll
            else:
                y = area.top
            for index, (message, height) in enumerate(blocks):
                appear = 1.0
                if index == len(blocks) - 1:
                    appear = min(1.0, (now - self._appear_started) / APPEAR_DURATION)
                self._draw_message(
                    surface, area, y, height, message, fonts, appear
                )
                y += height + MESSAGE_GAP
            if snapshot.busy:
                self._draw_working(surface, area, y, snapshot, fonts, now)
        finally:
            surface.set_clip(previous_clip)

        draw_scrollbar(surface, area, content_height, self.scroll)

    def _measure(
        self,
        messages: Sequence[ChatMessage],
        fonts: Dict[str, pygame.font.Font],
        width: int,
    ) -> List[Tuple[ChatMessage, int]]:
        """Measure each message block, caching wraps for immutable messages."""
        blocks: List[Tuple[ChatMessage, int]] = []
        body = fonts["body"]
        line_height = body.get_linesize() + LINE_GAP
        for message in messages:
            cache_key = (id(message), width)
            cached = self._wrap_cache.get(cache_key)
            if cached is not None and cached[0] == message.content:
                lines_count, label_height, text_width = cached[1], cached[2], cached[3]
            else:
                if message.role is ChatRole.USER:
                    text_width = min(width - 24, int(width * 0.76))
                elif message.role is ChatRole.ASSISTANT:
                    text_width = min(width - 8, int(width * 0.88))
                else:
                    text_width = width - 40
                lines = wrap(message.content, body, max(40, text_width - 20))
                lines_count = len(lines)
                label_height = 18 if message.role is not ChatRole.SYSTEM else 0
                if len(self._wrap_cache) > 64:
                    self._wrap_cache.clear()
                self._wrap_cache[cache_key] = (
                    message.content, lines_count, label_height, text_width
                )
            if message.role is ChatRole.USER:
                height = 20 + lines_count * line_height
            elif message.role is ChatRole.ASSISTANT:
                height = label_height + lines_count * line_height + 2
            else:
                height = lines_count * (fonts["small"].get_linesize() + 2)
            blocks.append((message, height))
        return blocks

    def _draw_message(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        y: float,
        height: int,
        message: ChatMessage,
        fonts: Dict[str, pygame.font.Font],
        appear: float,
    ) -> None:
        """One conversation entry, eased in on first appearance."""
        offset = int(8 * (1.0 - ease_out_cubic(appear)))
        alpha = int(255 * appear)
        y += offset

        body = fonts["body"]
        line_height = body.get_linesize() + LINE_GAP

        if message.role is ChatRole.USER:
            text_width = min(area.width - 24, int(area.width * 0.76))
            bubble = pygame.Rect(
                area.right - 20 - text_width, int(y), text_width, height
            )
            pygame.draw.rect(surface, (26, 31, 37), bubble, border_radius=14)
            pygame.draw.rect(
                surface, (43, 50, 58), bubble, 1, border_radius=14
            )
            self._blit_lines(
                surface, message.content, body, COLOR_TEXT,
                bubble.left + 12, y + 10, text_width - 24, line_height, alpha,
            )
            return

        if message.role is ChatRole.ASSISTANT:
            label = "VisionCore"
            tag = (
                f" · {message.source.label.lower()}"
                if message.source is not ResponseSource.SYSTEM
                else ""
            )
            header = fonts["small"].render(f"{label}{tag}", True, COLOR_TEXT_FAINT)
            header.set_alpha(alpha)
            surface.blit(header, (area.left + 4, y))
            text_width = min(area.width - 8, int(area.width * 0.88))
            self._blit_lines(
                surface, message.content, body, COLOR_TEXT,
                area.left + 4, y + 20, text_width - 20, line_height, alpha,
            )
            return

        # System line: centred, quiet, coloured by outcome.
        color = _NOTE_ACCENT.get(message.detail, COLOR_TEXT_FAINT)
        text_width = area.width - 40
        small = fonts["small"]
        line_y = y
        for line in wrap(message.content, small, max(40, text_width)):
            rendered = small.render(line, True, color)
            rendered.set_alpha(alpha)
            surface.blit(
                rendered,
                (area.centerx - rendered.get_width() // 2, line_y),
            )
            line_y += small.get_linesize() + 2

    @staticmethod
    def _blit_lines(
        surface: pygame.Surface,
        text: str,
        font: pygame.font.Font,
        color,
        x: int,
        y: float,
        width: int,
        line_height: int,
        alpha: int,
    ) -> None:
        for line in wrap(text, font, max(40, width)):
            rendered = font.render(line, True, color)
            rendered.set_alpha(alpha)
            surface.blit(rendered, (x, int(y)))
            y += line_height

    @staticmethod
    def _draw_working(
        surface: pygame.Surface,
        area: pygame.Rect,
        y: float,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
        now: float,
    ) -> None:
        """Real activity only: shown while the assistant is genuinely working."""
        dots = "." * (1 + int(now * 2) % 3)
        color = ai_status_color(snapshot.status)
        rendered = fonts["small"].render(f"{snapshot.status_label}{dots}", True, color)
        surface.blit(rendered, (area.left + 4, int(y) + 4))

    # -- empty state ------------------------------------------------------- #

    def _draw_empty_state(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """A calm, helpful starting point."""
        center_x = area.centerx
        y = area.top + max(10, int(area.height * 0.16))

        mark = icons.icon("spark", 34, COLOR_ACCENT)
        surface.blit(mark, (center_x - mark.get_width() // 2, y))
        y += mark.get_height() + 18

        title = fonts["display"].render("VisionCore AI", True, COLOR_TEXT)
        surface.blit(title, (center_x - title.get_width() // 2, y))
        y += title.get_height() + 8

        subtitle = fonts["title"].render("How can I help?", True, COLOR_TEXT_DIM)
        surface.blit(subtitle, (center_x - subtitle.get_width() // 2, y))
        y += subtitle.get_height() + 14

        description = (
            "I can help you understand VisionCore's state, explain its gestures "
            "and safety behaviour, and carry out the controls it supports."
        )
        for line in wrap(description, fonts["caption"], min(460, area.width - 40)):
            rendered = fonts["caption"].render(line, True, COLOR_TEXT_FAINT)
            surface.blit(rendered, (center_x - rendered.get_width() // 2, y))
            y += rendered.get_height() + 2
        y += 26

        # Suggestion cards, two per row when the column allows it.
        card_height = 42
        two_per_row = area.width >= 560
        columns = 2 if two_per_row else 1
        gap = 10
        card_width = (
            (min(520, area.width - 40) - gap) // 2 if two_per_row
            else min(360, area.width - 40)
        )
        mouse = pygame.mouse.get_pos()
        for index, suggestion in enumerate(SUGGESTIONS):
            row, column = divmod(index, columns)
            card = pygame.Rect(
                center_x - (card_width * columns + gap * (columns - 1)) // 2
                + column * (card_width + gap),
                y + row * (card_height + gap),
                card_width,
                card_height,
            )
            if card.bottom > area.bottom:
                break
            hovered = card.collidepoint(mouse)
            if hovered:
                pygame.draw.rect(surface, (23, 32, 40), card, border_radius=10)
                pygame.draw.rect(surface, COLOR_ACCENT, card, 1, border_radius=10)
            else:
                pygame.draw.rect(surface, COLOR_BORDER, card, 1, border_radius=10)
            text = fonts["body"].render(suggestion, True,
                                        COLOR_TEXT if hovered else COLOR_TEXT_DIM)
            surface.blit(text, (card.left + 14, card.centery - text.get_height() // 2))
            self._actions[f"suggest:{suggestion}"] = card

    # -- confirmation ------------------------------------------------------ #

    def _draw_confirmation(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Explicit two-step confirmation for a proposed action."""
        pygame.draw.rect(surface, COLOR_WARNING_TINT, rect, border_radius=10)
        pygame.draw.rect(surface, COLOR_WARNING, rect, 1, border_radius=10)
        pygame.draw.circle(surface, COLOR_WARNING, (rect.left + 14, rect.centery), 3)

        label = fit(
            f"Confirm “{snapshot.pending.label}”?", fonts["body"],
            rect.width - 200,
        )
        text = fonts["body"].render(label, True, COLOR_TEXT)
        surface.blit(text, (rect.left + 24, rect.centery - text.get_height() // 2))

        cancel = pygame.Rect(rect.right - 96, rect.centery - 14, 88, 28)
        confirm = pygame.Rect(cancel.left - 100, rect.centery - 14, 92, 28)
        draw_button(
            surface, confirm, "Confirm", fonts, kind="success",
            hovered=confirm.collidepoint(pygame.mouse.get_pos()),
        )
        draw_button(
            surface, cancel, "Cancel", fonts, kind="danger",
            hovered=cancel.collidepoint(pygame.mouse.get_pos()),
        )
        self._actions["confirm"] = confirm
        self._actions["cancel"] = cancel

    # -- voice line -------------------------------------------------------- #

    def _voice_line_height(self) -> int:
        """The microphone line: shown whenever it has something real to say."""
        state = self._voice.state
        if state is VoiceState.OFF:
            # A resting microphone needs no notice; only a quiet hint while the
            # composer is empty does the discoverability work.
            return 18
        return 20

    def _draw_voice_line(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        voice = self._voice
        state = voice.state
        color = voice_state_color(state)

        if state is VoiceState.OFF:
            hint = "Press V or click the microphone to speak"
            if not self.input_text.strip() and voice.available:
                text = fonts["small"].render(hint, True, COLOR_TEXT_FAINT)
                surface.blit(text, (rect.left + 2, rect.top))
            elif not voice.available and (voice.note or voice.detail):
                reason = fit(
                    f"Voice unavailable — {voice.note or voice.detail}",
                    fonts["small"], rect.width - 4,
                )
                text = fonts["small"].render(reason, True, COLOR_TEXT_FAINT)
                surface.blit(text, (rect.left + 2, rect.top))
            return

        pygame.draw.circle(surface, color, (rect.left + 6, rect.centery), 3)
        x = rect.left + 16

        measured = voice.level
        if state.listening and measured is not None:
            elapsed = fonts["small"].render(
                f"Listening · {voice.listening_seconds:.1f}s of {voice.limit_seconds:.0f}s",
                True, COLOR_TEXT_DIM,
            )
            surface.blit(elapsed, (x, rect.top))
            meter_left = x + elapsed.get_width() + 12
            meter = pygame.Rect(meter_left, rect.centery - 2,
                                max(30, rect.right - meter_left - 110), 5)
            self._draw_level(surface, meter, measured, color)
            hint = fonts["small"].render("V cancels", True, COLOR_TEXT_FAINT)
            surface.blit(hint, (rect.right - hint.get_width(), rect.top))
        else:
            detail = fit(
                self._voice_detail(voice), fonts["small"], rect.width - 20
            )
            text = fonts["small"].render(detail, True, COLOR_TEXT_DIM)
            surface.blit(text, (x, rect.top))

    @staticmethod
    def _voice_detail(voice: VoiceSnapshot) -> str:
        """What the microphone state means, in as few words as fit."""
        state = voice.state
        reason = voice.note or voice.detail
        if state is VoiceState.LISTENING:
            return (
                f"Listening · {voice.listening_seconds:.1f}s of "
                f"{voice.limit_seconds:.0f}s — V cancels"
            )
        if state is VoiceState.PROCESSING:
            return f"Transcribing locally ({voice.engine})"
        if state is VoiceState.READY:
            return f"Transcript sent ({voice.engine})"
        if state is VoiceState.UNAVAILABLE:
            return reason or "No local speech engine"
        if state is VoiceState.ERROR:
            return reason or "Recognition failed — press V to retry"
        if voice.note:
            return f"{voice.note} — press V to listen"
        return f"Press V to listen ({voice.engine})"

    def _draw_level(
        self,
        surface: pygame.Surface,
        meter: pygame.Rect,
        level: float,
        color,
    ) -> None:
        """A meter driven by measured amplitude only.

        The value comes from the recogniser's own audio callback. It is smoothed
        (quick attack, slow release) so a single frame of audio does not blink,
        and it is shown exactly as measured: a silent microphone lights nothing.
        """
        target = max(0.0, min(1.0, float(level)))
        rate = LEVEL_ATTACK if target > self._level_shown else LEVEL_RELEASE
        self._level_shown += (target - self._level_shown) * rate
        shown = max(0.0, min(1.0, self._level_shown))

        lit = int(round(shown * LEVEL_SEGMENTS))
        gap = 2
        segment = max(2, (meter.width - gap * (LEVEL_SEGMENTS - 1)) // LEVEL_SEGMENTS)
        for index in range(LEVEL_SEGMENTS):
            bar = pygame.Rect(
                meter.left + index * (segment + gap), meter.top, segment, meter.height
            )
            pygame.draw.rect(surface, color if index < lit else (36, 41, 47), bar,
                             border_radius=1)

    # -- composer ---------------------------------------------------------- #

    def _draw_composer(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
        now: float,
    ) -> None:
        mouse = pygame.mouse.get_pos()

        pygame.draw.rect(surface, (22, 24, 28), rect, border_radius=14)
        pygame.draw.rect(surface, COLOR_BORDER_STRONG, rect, 1, border_radius=14)

        # Microphone button, anchored left.
        voice = self._voice
        mic_rect = pygame.Rect(rect.left + 9, rect.centery - 17, 34, 34)
        self._microphone_rect = mic_rect
        self._actions["microphone"] = mic_rect
        state = voice.state
        if state.active:
            pulse = 0.5 + 0.5 * math.sin(now * 5.0)
            mic_color = COLOR_WARNING if state.listening else COLOR_ACCENT
            edge = tuple(int(c * (0.5 + 0.5 * pulse)) for c in mic_color)
            pygame.draw.rect(surface, (40, 33, 22), mic_rect, border_radius=17)
            pygame.draw.rect(surface, edge, mic_rect, 1, border_radius=17)
            icon_color = mic_color
        elif voice.available:
            hovered = mic_rect.collidepoint(mouse)
            pygame.draw.rect(
                surface, (28, 31, 36) if hovered else (26, 29, 33),
                mic_rect, border_radius=17,
            )
            pygame.draw.rect(
                surface, COLOR_BORDER_STRONG if hovered else COLOR_BORDER,
                mic_rect, 1, border_radius=17,
            )
            icon_color = COLOR_TEXT_DIM if hovered else COLOR_TEXT_FAINT
        else:
            icon_color = COLOR_DISABLED
        mic_icon = icons.icon("mic", 16, icon_color)
        surface.blit(
            mic_icon,
            (mic_rect.centerx - mic_icon.get_width() // 2,
             mic_rect.centery - mic_icon.get_height() // 2),
        )

        # Send button, anchored right.
        send_rect = pygame.Rect(rect.right - 43, rect.centery - 17, 34, 34)
        self._send_rect = send_rect
        can_send = bool(self.input_text.strip()) and not snapshot.in_flight
        self._actions["send"] = send_rect
        if can_send:
            hovered = send_rect.collidepoint(mouse)
            pygame.draw.rect(
                surface, (30, 43, 54) if hovered else (23, 32, 40),
                send_rect, border_radius=17,
            )
            pygame.draw.rect(
                surface, COLOR_ACCENT if hovered else (60, 100, 130),
                send_rect, 1, border_radius=17,
            )
            send_icon = icons.icon("send", 16, COLOR_ACCENT_STRONG)
        else:
            send_icon = icons.icon("send", 16, COLOR_DISABLED)
        surface.blit(
            send_icon,
            (send_rect.centerx - send_icon.get_width() // 2,
             send_rect.centery - send_icon.get_height() // 2),
        )

        # Text area between the two buttons.
        text_left = mic_rect.right + 8
        text_width = send_rect.left - 8 - text_left
        line_height = fonts["body"].get_linesize() + 2
        count, shown = self._input_lines(fonts, text_width)

        if self.input_text:
            total_height = len(shown) * line_height
            first_y = rect.centery - total_height // 2
            for index, line in enumerate(shown):
                rendered = fonts["body"].render(line, True, COLOR_TEXT)
                surface.blit(rendered, (text_left, first_y + index * line_height))
            # Caret at the end of the last visible line.
            if (now % CARET_PERIOD) < CARET_PERIOD * 0.6:
                last = shown[-1]
                last_surf = fonts["body"].render(last, True, COLOR_TEXT)
                caret_x = min(
                    rect.right - 52, text_left + last_surf.get_width() + 1
                )
                caret_y = first_y + (len(shown) - 1) * line_height
                pygame.draw.line(
                    surface, COLOR_ACCENT,
                    (caret_x, caret_y + 3), (caret_x, caret_y + line_height - 5), 1,
                )
        else:
            hint = fonts["body"].render("Ask VisionCore anything…", True, COLOR_TEXT_FAINT)
            surface.blit(hint, (text_left, rect.centery - hint.get_height() // 2))
            if (now % CARET_PERIOD) < CARET_PERIOD * 0.6:
                pygame.draw.line(
                    surface, COLOR_ACCENT,
                    (text_left, rect.top + 16), (text_left, rect.bottom - 16), 1,
                )
