"""The VisionCore AI panel: a restrained, integrated conversation surface.

The panel is a floating glass module over the camera viewport. It reuses the
interface's own visual language - the same palette, the chamfered corners, the
thin cyan rules and the mono/caption type scale - rather than looking like a web
chat widget dropped into the window. Three rules shape it:

* the assistant's real status is always on screen (READY, PROCESSING, RESPONDING,
  EXECUTING, ERROR, NOT CONFIGURED), never an animation standing in for work that
  is not happening;
* a reply is labelled with the source it really came from, so a deterministic
  local answer is never presented as a model answer;
* the panel owns no application state beyond the text being typed: everything it
  shows comes from the published :class:`~app.ai.types.AISnapshot`.

Since Phase 8 the panel also carries the microphone control. It is a single row
under the conversation, it is quiet, and it only ever shows the real state of the
voice layer: ``MIC OFF`` with a TALK control, ``MIC LISTENING`` with an elapsed
timer, a level meter fed by *measured* amplitude (an engine that reports nothing
draws nothing), ``MIC PROCESSING`` while a transcript is being produced, and
``MIC UNAVAILABLE`` with the real reason when the machine cannot listen at all.
Microphone audio and transcripts stay between the panel row and the voice layer;
this module never sees a sample.
"""

from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.ai.types import ChatMessage, ChatRole, ResponseSource
from app.voice.types import VoiceSnapshot, VoiceState
from ui.hud import (
    ai_status_color,
    COLOR_CYAN_PRIMARY,
    COLOR_DISABLED,
    COLOR_ERROR,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BORDER,
    COLOR_STANDBY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    voice_state_color,
)

# Panel geometry bounds (screen pixels).
MIN_PANEL_WIDTH = 260
MAX_PANEL_WIDTH = 430
MIN_PANEL_HEIGHT = 250
MAX_PANEL_HEIGHT = 460
MARGIN = 10

HEADER_HEIGHT = 46
STATUS_HEIGHT = 32
INPUT_HEIGHT = 30
CONFIRM_HEIGHT = 30
VOICE_HEIGHT = 26
PAD = 12
BUBBLE_PAD = 8
GAP = 8
LINE_GAP = 2
MAX_BLOCKS = 40
MAX_INPUT_CHARS = 600
CARET_PERIOD = 1.0
WORKING_HEIGHT = 24
# Segments in the microphone level meter and how much the displayed level moves
# towards the measured one each frame (fast attack, slow release).
LEVEL_SEGMENTS = 12
LEVEL_ATTACK = 0.55
LEVEL_RELEASE = 0.10

# Accent for an assistant-generated result line, keyed by its detail tag.
_NOTE_ACCENT = {
    "RESULT": COLOR_ICE_BLUE,
    "CONFIRM": COLOR_STANDBY,
    "EXPIRED": COLOR_STANDBY,
    "SUPERSEDED": COLOR_STANDBY,
    "REFUSED": COLOR_ERROR,
    "NO ROUTE": COLOR_ERROR,
}


def _wrap(text: str, font: pygame.font.Font, width: int) -> List[str]:
    """Wrap plain text to the given pixel width (breaking overlong words)."""
    lines: List[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else f"{current} {word}"
            if font.size(candidate)[0] <= width or not current:
                current = candidate
                continue
            lines.append(current)
            current = word
            while font.size(current)[0] > width and len(current) > 1:
                cut = len(current) - 1
                while cut > 1 and font.size(current[:cut])[0] > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines or [""]


def _fit(text: str, font: pygame.font.Font, width: int) -> str:
    """Shorten a single line until it fits, with an ellipsis marker."""
    if font.size(text)[0] <= width:
        return text
    trimmed = text
    while trimmed and font.size(trimmed + "...")[0] > width:
        trimmed = trimmed[:-1]
    return (trimmed + "...") if trimmed else ""


class AIPanel:
    """Draws the assistant and turns input into panel intents."""

    def __init__(self) -> None:
        self.input_text = ""
        self.scroll = 0.0
        self.rect: Optional[pygame.Rect] = None
        self._close_rect = pygame.Rect(0, 0, 0, 0)
        self._clear_rect = pygame.Rect(0, 0, 0, 0)
        self._send_rect = pygame.Rect(0, 0, 0, 0)
        self._confirm_rect = pygame.Rect(0, 0, 0, 0)
        self._cancel_rect = pygame.Rect(0, 0, 0, 0)
        self._strip_rect = pygame.Rect(0, 0, 0, 0)
        self._voice_rect = pygame.Rect(0, 0, 0, 0)
        self._microphone_rect = pygame.Rect(0, 0, 0, 0)
        self._conversation_rect = pygame.Rect(0, 0, 0, 0)
        self._blocks = 0
        self._level_shown = 0.0
        self._voice: VoiceSnapshot = VoiceSnapshot()

    # -- state ------------------------------------------------------------- #

    def clear_input(self) -> None:
        self.input_text = ""

    def consume_input(self) -> str:
        """Take the typed message (the caller clears the row by consuming it)."""
        text = self.input_text.strip()
        self.input_text = ""
        return text

    def contains(self, position: Tuple[int, int]) -> bool:
        return self.rect is not None and self.rect.collidepoint(position)

    # -- events ------------------------------------------------------------ #

    def handle_key(self, event: pygame.event.Event) -> Optional[str]:
        """Type into the input row. Returns a panel intent when one is raised."""
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return "send"
        if event.key == pygame.K_BACKSPACE:
            self.input_text = self.input_text[:-1]
        elif event.key == pygame.K_DELETE:
            self.input_text = ""
        elif event.key == pygame.K_UP:
            self.scroll += 24
        elif event.key == pygame.K_DOWN:
            self.scroll = max(0.0, self.scroll - 24)
        else:
            character = getattr(event, "unicode", "")
            if character and character.isprintable() and len(self.input_text) < MAX_INPUT_CHARS:
                self.input_text += character
        return None

    def handle_wheel(self, dy: int) -> bool:
        """Scroll the conversation when the pointer is over it."""
        if not dy or not self._conversation_rect.collidepoint(pygame.mouse.get_pos()):
            return False
        self.scroll = max(0.0, self.scroll + dy * 28)
        return True

    def handle_click(self, position: Tuple[int, int]) -> Optional[str]:
        """Route a click inside the panel to a panel intent."""
        if not self.contains(position):
            return None
        if self._close_rect.collidepoint(position):
            return "close"
        if self._clear_rect.collidepoint(position):
            return "clear"
        if self._send_rect.collidepoint(position):
            return "send"
        if self._microphone_rect.collidepoint(position):
            return "microphone"
        if self._strip_rect.collidepoint(position):
            if self._confirm_rect.collidepoint(position):
                return "confirm"
            if self._cancel_rect.collidepoint(position):
                return "cancel"
        return "focus"

    # -- layout ------------------------------------------------------------ #

    def layout(self, viewport: pygame.Rect, content_height: int) -> pygame.Rect:
        """Place the panel inside the viewport, sized for the window."""
        width = max(MIN_PANEL_WIDTH, min(MAX_PANEL_WIDTH, int(viewport.width * 0.52)))
        width = min(width, max(200, viewport.width - 2 * MARGIN))
        height = max(MIN_PANEL_HEIGHT, min(MAX_PANEL_HEIGHT, int(content_height * 0.86)))
        height = min(height, max(180, content_height - 2 * MARGIN))
        self.rect = pygame.Rect(
            viewport.right - width - MARGIN, viewport.top + MARGIN, width, height
        )
        self._layout_parts()
        return self.rect

    def _layout_parts(self) -> None:
        rect = self.rect
        if rect is None:
            return
        self._close_rect = pygame.Rect(rect.right - 30, rect.top + 12, 20, 20)
        self._clear_rect = pygame.Rect(self._close_rect.left - 58, rect.top + 12, 52, 20)
        self._send_rect = pygame.Rect(rect.right - PAD - 54, rect.bottom - INPUT_HEIGHT - 8, 54, 22)

        # Rows are stacked from the bottom: input, microphone, confirmation. The
        # microphone row sits above the typing field so the control is always in
        # the same place and can never be clipped; the confirmation strip is only
        # drawn while a proposal is pending.
        input_top = rect.bottom - INPUT_HEIGHT - 8
        voice_top = input_top - VOICE_HEIGHT - 6
        self._voice_rect = pygame.Rect(
            rect.left + PAD, voice_top, rect.width - 2 * PAD, VOICE_HEIGHT
        )
        self._microphone_rect = pygame.Rect(
            rect.right - PAD - 58, voice_top + 2, 58, VOICE_HEIGHT - 4
        )

        strip_top = voice_top - CONFIRM_HEIGHT - 6
        self._strip_rect = pygame.Rect(
            rect.left + PAD, strip_top, rect.width - 2 * PAD, CONFIRM_HEIGHT
        )
        self._cancel_rect = pygame.Rect(rect.right - PAD - 76, strip_top + 4, 70, 22)
        self._confirm_rect = pygame.Rect(self._cancel_rect.left - 88, strip_top + 4, 80, 22)

    def _conversation_bounds(self, has_confirm: bool, has_line: bool) -> pygame.Rect:
        """Space left for the conversation once the rows above it are placed.

        ``has_line`` covers both the provider note and the context line: either
        one occupies the second status row, and the conversation may not overlap
        it - the drawing is clipped to this rectangle.
        """
        rect = self.rect
        top = rect.top + HEADER_HEIGHT + (STATUS_HEIGHT if has_line else 20)
        bottom = self._voice_rect.top - 4
        if has_confirm:
            bottom = self._strip_rect.top - 4
        return pygame.Rect(
            rect.left + PAD, top, rect.width - 2 * PAD, max(30, bottom - top)
        )

    # -- rendering --------------------------------------------------------- #

    def draw(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        snapshot,
        context_line: str,
        fonts: Dict[str, pygame.font.Font],
        now: Optional[float] = None,
        voice=None,
    ) -> None:
        """Draw the panel from the assistant snapshot.

        ``voice`` is the published :class:`~app.voice.types.VoiceSnapshot`. It is
        optional so the panel can be drawn on its own; with nothing published the
        row reports the microphone as unavailable rather than guessing at it.
        """
        self.rect = rect
        self._layout_parts()
        now = time.perf_counter() if now is None else now
        self._voice = voice if voice is not None else VoiceSnapshot()

        self._draw_glass(surface, rect)

        messages: Sequence[ChatMessage] = tuple(snapshot.messages)[-MAX_BLOCKS:]
        if len(messages) != self._blocks:
            self._blocks = len(messages)
            self.scroll = 0.0

        note = self._status_note(snapshot)
        self._draw_header(surface, rect, fonts)
        self._draw_status(surface, rect, snapshot, note, context_line, fonts)

        conversation = self._conversation_bounds(
            snapshot.pending is not None, bool(note or context_line)
        )
        self._conversation_rect = conversation
        self._draw_conversation(surface, conversation, messages, snapshot, fonts)
        if snapshot.pending is not None:
            self._draw_confirmation(surface, snapshot, fonts)
        self._draw_voice(surface, self._voice, fonts)
        self._draw_input(surface, rect, snapshot, fonts, now)

    # -- parts ------------------------------------------------------------- #

    @staticmethod
    def _draw_glass(surface: pygame.Surface, rect: pygame.Rect) -> None:
        """Chamfered, faintly translucent panel that matches the interface."""
        glass = pygame.Surface(rect.size, pygame.SRCALPHA)
        chamfer = 10
        w, h = rect.size
        c = min(chamfer, w // 4, h // 4)
        points = [
            (c, 0),
            (w, 0),
            (w, h - c),
            (w - c, h),
            (0, h),
            (0, c),
        ]
        pygame.draw.polygon(glass, (11, 19, 30, 232), points)
        pygame.draw.polygon(glass, (*COLOR_PANEL_BORDER, 235), points, 1)
        pygame.draw.line(glass, COLOR_CYAN_PRIMARY, (0, c), (c, 0), 2)
        pygame.draw.line(glass, COLOR_CYAN_PRIMARY, (w - c, h - 1), (w - 1, h - c), 2)
        surface.blit(glass, rect.topleft)

    def _draw_header(
        self, surface: pygame.Surface, rect: pygame.Rect, fonts: Dict[str, pygame.font.Font]
    ) -> None:
        title = fonts["subheading"].render("VISIONCORE AI", True, COLOR_TEXT_WHITE)
        surface.blit(title, (rect.left + PAD, rect.top + 8))
        subtitle = fonts["caption"].render("SYSTEM ASSISTANT", True, COLOR_CYAN_PRIMARY)
        surface.blit(subtitle, (rect.left + PAD, rect.top + 28))
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left + PAD, rect.top + HEADER_HEIGHT - 4),
            (rect.right - PAD, rect.top + HEADER_HEIGHT - 4),
            1,
        )

        hud_font = fonts["mono_small"]
        mouse = pygame.mouse.get_pos()
        clear_color = COLOR_TEXT_WHITE if self._clear_rect.collidepoint(mouse) else COLOR_TEXT_MUTED
        clear = hud_font.render("CLEAR", True, clear_color)
        surface.blit(clear, clear.get_rect(center=self._clear_rect.center))

        close_color = COLOR_ERROR if self._close_rect.collidepoint(mouse) else COLOR_TEXT_MUTED
        pygame.draw.line(
            surface,
            close_color,
            (self._close_rect.left + 6, self._close_rect.top + 6),
            (self._close_rect.right - 6, self._close_rect.bottom - 6),
            2,
        )
        pygame.draw.line(
            surface,
            close_color,
            (self._close_rect.right - 6, self._close_rect.top + 6),
            (self._close_rect.left + 6, self._close_rect.bottom - 6),
            2,
        )

    @staticmethod
    def _status_note(snapshot) -> str:
        """Secondary line: the real provider/error note, when there is one."""
        if snapshot.error is not None:
            detail = snapshot.error_detail or ""
            return f"{snapshot.error.label} / {detail}" if detail else snapshot.error.label
        if not snapshot.configured:
            return snapshot.note or "NO PROVIDER CONFIGURED / LOCAL ANSWERS ACTIVE"
        return ""

    def _draw_status(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        snapshot,
        note: str,
        context_line: str,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        y = rect.top + HEADER_HEIGHT
        color = ai_status_color(snapshot.status)
        pygame.draw.circle(surface, color, (rect.left + PAD + 4, y + 9), 4)
        status = fonts["mono"].render(snapshot.status_label, True, color)
        surface.blit(status, (rect.left + PAD + 14, y + 2))

        provider = snapshot.header_note.upper() if snapshot.configured else "LOCAL"
        waiting = f"  {snapshot.pending_seconds:.0f}s" if snapshot.pending is not None else ""
        meta = fonts["mono_small"].render(f"{provider}{waiting}", True, COLOR_TEXT_MUTED)
        surface.blit(meta, meta.get_rect(topright=(rect.right - PAD, y + 4)))

        line = note or context_line
        if not line:
            return
        # Only a real failure is shown in the error colour; an unconfigured
        # assistant is a fact, not a fault.
        line_color = COLOR_ERROR if snapshot.error is not None else COLOR_TEXT_MUTED
        fitted = _fit(line, fonts["mono_small"], rect.width - 2 * PAD)
        surface.blit(fonts["mono_small"].render(fitted, True, line_color), (rect.left + PAD, y + 15))

    def _draw_conversation(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        messages: Sequence[ChatMessage],
        snapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Draw the conversation, pinned to the newest entry."""
        blocks = self._measure(messages, fonts, area.width)
        content_height = (
            sum(height for _, height in blocks) + GAP * max(0, len(blocks) - 1)
        )
        if not blocks:
            content_height = 22
        if snapshot.busy:
            content_height += WORKING_HEIGHT

        max_scroll = max(0.0, content_height - area.height)
        self.scroll = min(self.scroll, max_scroll)

        previous_clip = surface.get_clip()
        surface.set_clip(area)
        try:
            # Short conversations read from the top; once the history is taller
            # than the panel it stays pinned to the newest entry at the bottom.
            if content_height > area.height:
                y = area.bottom - content_height + self.scroll
            else:
                y = area.top
            if not blocks and not snapshot.busy:
                empty = fonts["caption"].render(
                    "Ask about the camera, tracking, gestures or the current mode, "
                    "or press V and speak.",
                    True,
                    COLOR_TEXT_MUTED,
                )
                surface.blit(empty, (area.left, y))
            for message, height in blocks:
                self._draw_bubble(surface, area, y, height, message, fonts)
                y += height + GAP
            if snapshot.busy:
                self._draw_working(surface, area, y, snapshot, fonts)
        finally:
            surface.set_clip(previous_clip)

        if max_scroll > 1.0:
            self._draw_scrollbar(surface, area, content_height, max_scroll)

    @staticmethod
    def _measure(
        messages: Sequence[ChatMessage],
        fonts: Dict[str, pygame.font.Font],
        width: int,
    ) -> List[Tuple[ChatMessage, int]]:
        blocks: List[Tuple[ChatMessage, int]] = []
        text_width = max(60, width - 2 * BUBBLE_PAD - 6)
        for message in messages:
            lines = _wrap(message.content, fonts["body"], text_width)
            height = 16 + len(lines) * (fonts["body"].get_linesize() + LINE_GAP) + 2 * BUBBLE_PAD
            blocks.append((message, height))
        return blocks

    def _draw_bubble(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        y: int,
        height: int,
        message: ChatMessage,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """One entry: role label, optional source tag, wrapped text."""
        if message.role is ChatRole.USER:
            accent = COLOR_CYAN_PRIMARY
        elif message.role is ChatRole.SYSTEM:
            accent = _NOTE_ACCENT.get(message.detail, COLOR_TEXT_MUTED)
        else:
            accent = COLOR_ICE_BLUE

        # A message is labelled with how it arrived and where the answer came
        # from: YOU - VOICE for a transcript, VISIONCORE - LOCAL for a
        # deterministic answer, SYSTEM for a gate result.
        if message.role is ChatRole.USER:
            tag_text, tag_color = message.source_label, COLOR_CYAN_PRIMARY
        elif message.source is not ResponseSource.SYSTEM:
            tag_text, tag_color = message.source.label, COLOR_TEXT_MUTED
        else:
            tag_text, tag_color = "", COLOR_TEXT_MUTED

        label = fonts["mono_small"].render(message.role.label.upper(), True, accent)
        surface.blit(label, (area.left + BUBBLE_PAD, y))
        if tag_text:
            tag = fonts["mono_small"].render(tag_text, True, tag_color)
            surface.blit(tag, (area.left + BUBBLE_PAD + label.get_width() + 8, y))

        bubble = pygame.Rect(area.left, y + 14, area.width, height - 14)
        if message.role is ChatRole.USER:
            pygame.draw.rect(surface, (14, 28, 44), bubble)
        elif message.role is ChatRole.ASSISTANT:
            pygame.draw.rect(surface, (12, 22, 36), bubble)
        pygame.draw.line(
            surface, accent, (bubble.left, bubble.top + 2), (bubble.left, bubble.bottom - 2), 2
        )
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (bubble.left + 2, bubble.bottom - 1),
            (bubble.right, bubble.bottom - 1),
            1,
        )

        text_color = COLOR_TEXT_MUTED if message.role is ChatRole.SYSTEM else COLOR_TEXT_WHITE
        line_height = fonts["body"].get_linesize() + LINE_GAP
        text_y = bubble.top + BUBBLE_PAD - 2
        for line in _wrap(
            message.content, fonts["body"], max(60, area.width - 2 * BUBBLE_PAD - 6)
        ):
            surface.blit(
                fonts["body"].render(line, True, text_color),
                (bubble.left + BUBBLE_PAD + 6, text_y),
            )
            text_y += line_height

    @staticmethod
    def _draw_working(
        surface: pygame.Surface,
        area: pygame.Rect,
        y: int,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Real activity only: shown while the assistant is genuinely working."""
        dots = "." * (1 + int((time.perf_counter() * 2) % 3))
        color = ai_status_color(snapshot.status)
        rendered = fonts["mono_small"].render(f"{snapshot.status_label}{dots}", True, color)
        surface.blit(rendered, (area.left + BUBBLE_PAD, y + 4))

    def _draw_scrollbar(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        content_height: float,
        max_scroll: float,
    ) -> None:
        """Thin indicator; 0 means pinned to the newest entry at the bottom."""
        track = max(20, area.height - 4)
        thumb = max(18, int(track * min(1.0, area.height / max(area.height, content_height))))
        fraction = 1.0 - min(1.0, max(0.0, self.scroll / max_scroll))
        top = area.top + int((track - thumb) * fraction)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, pygame.Rect(area.right - 3, top, 3, thumb))

    def _draw_confirmation(
        self,
        surface: pygame.Surface,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Two step confirmation for a disruptive action."""
        strip = self._strip_rect
        pygame.draw.rect(surface, (28, 22, 10), strip)
        pygame.draw.rect(surface, COLOR_STANDBY, strip, 1)
        label_width = self._confirm_rect.left - strip.left - 12
        label = _fit(
            f"CONFIRM {snapshot.pending.label}?", fonts["mono_small"], max(20, label_width)
        )
        surface.blit(
            fonts["mono_small"].render(label, True, COLOR_STANDBY),
            (strip.left + 8, strip.top + 7),
        )
        self._draw_button(surface, self._confirm_rect, "CONFIRM", COLOR_ONLINE, fonts, True)
        self._draw_button(surface, self._cancel_rect, "CANCEL", COLOR_ERROR, fonts, True)

    def _draw_voice(
        self,
        surface: pygame.Surface,
        voice: VoiceSnapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """The microphone row: real state, real level, one deliberate control.

        Drawn for every microphone state, including ``UNAVAILABLE``: on a machine
        that cannot listen, the row says so instead of leaving the user to find
        out by pressing the key. Everything shown here comes from the published
        :class:`~app.voice.types.VoiceSnapshot`, so the row can never claim the
        microphone is open while it is closed.
        """
        row = self._voice_rect
        state = voice.state
        listening = state.listening
        color = voice_state_color(state)

        pygame.draw.rect(surface, (10, 19, 30), row)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, row, 1)

        # A slow pulse on the row edge and on the status dot, while the
        # microphone is genuinely open. The pulse is driven by the state, never
        # by the meter: a silent room still shows an open microphone.
        pulse = 0.5 + 0.5 * math.sin(time.perf_counter() * 3.0)
        dot = color
        if listening:
            edge = tuple(int(channel * (0.35 + 0.65 * pulse)) for channel in COLOR_STANDBY)
            pygame.draw.line(
                surface, edge, (row.left + 1, row.top), (row.left + 1, row.bottom), 2
            )
            dot = tuple(int(channel * (0.5 + 0.5 * pulse)) for channel in color)
        pygame.draw.circle(surface, dot, (row.left + 11, row.centery), 3)

        label = fonts["mono_small"].render(f"MIC {voice.status_label}", True, color)
        surface.blit(label, (row.left + 19, row.centery - label.get_height() // 2))

        detail_x = row.left + 19 + label.get_width() + 10
        hint = fonts["mono_small"].render("[V]", True, COLOR_TEXT_MUTED)
        meter_right = self._microphone_rect.left - hint.get_width() - 16
        room = max(20, meter_right - detail_x)

        measured = voice.level
        if listening and measured is not None:
            # The engine is measuring the microphone: show the measurement and
            # the elapsed window, so "listening" is backed by real amplitude.
            meter = pygame.Rect(detail_x, row.centery - 3, room, 6)
            self._draw_level(surface, meter, measured, color)
        else:
            detail = _fit(self._voice_detail(voice), fonts["mono_small"], room)
            if detail:
                text = fonts["mono_small"].render(detail, True, COLOR_TEXT_MUTED)
                surface.blit(text, (detail_x, row.centery - text.get_height() // 2))

        surface.blit(
            hint, hint.get_rect(midright=(self._microphone_rect.left - 6, row.centery))
        )

        if state.active:
            label_text, button_color, enabled = "CANCEL", COLOR_ERROR, True
        elif voice.available:
            label_text, button_color, enabled = "TALK", color, True
        else:
            # No engine or no input device: the control is inert, not a promise.
            label_text, button_color, enabled = "MIC", COLOR_DISABLED, False
        self._draw_button(
            surface, self._microphone_rect, label_text, button_color, fonts, enabled
        )

    @staticmethod
    def _voice_detail(voice: VoiceSnapshot) -> str:
        """What the microphone state means, in as few words as fit.

        ``note`` is whatever the layer last reported - a timeout, a cancellation,
        a failure or the engine in use - so the row explains the state instead of
        only naming it.
        """
        state = voice.state
        reason = voice.note or voice.detail
        if state is VoiceState.LISTENING:
            return (
                f"{voice.listening_seconds:.1f}s OF {voice.limit_seconds:.0f}s"
                " - V CANCELS"
            )
        if state is VoiceState.PROCESSING:
            return f"TRANSCRIBING LOCALLY ({voice.engine})"
        if state is VoiceState.READY:
            return f"TRANSCRIPT SENT ({voice.engine})"
        if state is VoiceState.UNAVAILABLE:
            return reason or "NO LOCAL SPEECH ENGINE"
        if state is VoiceState.ERROR:
            return reason or "RECOGNITION FAILED - PRESS V TO RETRY"
        if voice.note:
            return f"{voice.note} - PRESS V TO LISTEN"
        return f"PRESS V TO LISTEN ({voice.engine})"

    def _draw_level(
        self,
        surface: pygame.Surface,
        meter: pygame.Rect,
        level: float,
        color: Tuple[int, int, int],
    ) -> None:
        """A meter driven by measured amplitude only.

        The value comes from the recogniser's own audio callback. It is smoothed
        (quick attack, slow release) so a single frame of audio does not blink,
        and it is shown exactly as measured: a silent microphone lights nothing.
        No synthetic waveform is ever drawn, because a meter that moved on its
        own would be a lie about whether the microphone is working.
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
            pygame.draw.rect(
                surface, color if index < lit else (24, 38, 54), bar
            )

    def _draw_input(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        snapshot,
        fonts: Dict[str, pygame.font.Font],
        now: float,
    ) -> None:
        field = pygame.Rect(
            rect.left + PAD,
            rect.bottom - INPUT_HEIGHT - 8,
            max(60, self._send_rect.left - 8 - (rect.left + PAD)),
            INPUT_HEIGHT,
        )
        pygame.draw.rect(surface, (9, 17, 27), field)
        pygame.draw.rect(surface, COLOR_PANEL_BORDER, field, 1)
        pygame.draw.line(
            surface, COLOR_CYAN_PRIMARY, (field.left, field.top), (field.left + 10, field.top), 2
        )

        if self.input_text:
            shown = _fit(self.input_text, fonts["body"], field.width - 16)
            text = fonts["body"].render(shown, True, COLOR_TEXT_WHITE)
            surface.blit(text, (field.left + 8, field.centery - text.get_height() // 2))
            if (now % CARET_PERIOD) < CARET_PERIOD * 0.6:
                caret_x = min(field.right - 4, field.left + 10 + text.get_width())
                pygame.draw.line(
                    surface,
                    COLOR_CYAN_PRIMARY,
                    (caret_x, field.top + 6),
                    (caret_x, field.bottom - 6),
                    1,
                )
        else:
            hint = fonts["body"].render("Ask VisionCore...", True, COLOR_TEXT_MUTED)
            surface.blit(hint, (field.left + 8, field.centery - hint.get_height() // 2))

        self._draw_button(
            surface,
            self._send_rect,
            "SEND",
            COLOR_CYAN_PRIMARY,
            fonts,
            enabled=bool(self.input_text.strip()) and not snapshot.in_flight,
        )

    @staticmethod
    def _draw_button(
        surface: pygame.Surface,
        rect: pygame.Rect,
        label: str,
        color: Tuple[int, int, int],
        fonts: Dict[str, pygame.font.Font],
        enabled: bool,
    ) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        if not enabled:
            border, text_color, background = COLOR_DISABLED, COLOR_DISABLED, (10, 16, 24)
        else:
            border = color
            text_color = COLOR_TEXT_WHITE if hovered else color
            background = (18, 32, 48) if hovered else (12, 24, 38)
        pygame.draw.rect(surface, background, rect)
        pygame.draw.rect(surface, border, rect, 1)
        text = fonts["mono_small"].render(label, True, text_color)
        surface.blit(text, text.get_rect(center=rect.center))
