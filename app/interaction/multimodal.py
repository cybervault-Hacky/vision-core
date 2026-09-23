"""One input model for every way a deliberate request can reach VisionCore.

Phase 8 adds voice next to text and gestures, so the sources are named in one
place and every one of them is described the same way::

    GESTURE    a recognised pose               -> typed control intent
    TEXT       the assistant input row         -> query for the assistant
    VOICE      a local speech transcript       -> query for the assistant
    INTERFACE  a button or a key press         -> typed control intent
    AI         the assistant's own proposal    -> typed control intent

Two rules make this abstraction worth having rather than decorative:

* **Nothing here acts.** An envelope describes where a request came from and
  what it carries; the assistant answers it or turns it into a structured
  intent, and only the intent router can reach a controller.
* **Provenance is real.** Text, voice and interface requests travel the same
  pipeline, but the conversation still records how each message arrived, so the
  panel can show ``YOU · VOICE`` next to ``YOU · TEXT`` and a transcript is
  never mistaken for something the user typed.

Voice is a *source*, not a separate assistant: a transcript becomes an
:class:`InputEnvelope` with :attr:`InputSource.VOICE` and is handed to the same
:class:`~app.ai.assistant.AIAssistant` that a typed message uses, which is why
:attr:`InputSource.VOICE` maps onto the AI intent source rather than opening a
second route into the control layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.interaction.intent import IntentSource


class InputSource(str, Enum):
    """Where a deliberate request came from."""

    GESTURE = "GESTURE"
    TEXT = "TEXT"
    VOICE = "VOICE"
    INTERFACE = "INTERFACE"
    AI = "AI"

    @property
    def label(self) -> str:
        """Short upper-case tag used by the interface and the AI prompt."""
        return self.value

    @property
    def spoken(self) -> bool:
        """True when the input arrived as speech (or as a transcript of it)."""
        return self is InputSource.VOICE


    @property
    def intent_source(self) -> IntentSource:
        """The intent-router source this input travels under.

        Text and voice do not get their own route into the control layers: they
        are handled by the assistant, and an action produced from either carries
        :class:`~app.interaction.intent.IntentSource.AI`. Gestures and interface
        actions keep their own sources, and a gesture can therefore never be
        mistaken for a spoken command.
        """
        if self is InputSource.GESTURE:
            return IntentSource.GESTURE
        if self is InputSource.INTERFACE:
            return IntentSource.INTERFACE
        return IntentSource.AI


class InputKind(str, Enum):
    """What an input carries into the system."""

    QUERY = "QUERY"      # a question or a request in words; it may answer or resolve to an intent
    INTENT = "INTENT"    # already typed: a named action the router can carry out


@dataclass(frozen=True, slots=True)
class InputEnvelope:
    """One deliberate input, described uniformly before anything acts on it."""

    source: InputSource
    text: str = ""
    kind: InputKind = InputKind.QUERY
    timestamp: float = 0.0

    @classmethod
    def query(cls, source: InputSource, text: str, timestamp: float = 0.0) -> "InputEnvelope":
        """A request in words (typed or spoken)."""
        return cls(source=source, text=text, kind=InputKind.QUERY, timestamp=timestamp)

    @classmethod
    def action(cls, source: InputSource, timestamp: float = 0.0) -> "InputEnvelope":
        """An input that is already a typed action (gesture, button, assistant)."""
        return cls(source=source, kind=InputKind.INTENT, timestamp=timestamp)

    @property
    def label(self) -> str:
        return self.source.label

    @property
    def intent_source(self) -> IntentSource:
        return self.source.intent_source

    @property
    def empty(self) -> bool:
        """True when a query carries no words (nothing to answer)."""
        return self.kind is InputKind.QUERY and not self.text.strip()

    def summary(self) -> str:
        """Compact description used by logs: ``VOICE QUERY: "turn it up"``."""
        if self.kind is InputKind.INTENT:
            return f"{self.source.label} INTENT"
        return f'{self.source.label} QUERY: "{self.text[:60]}"'
