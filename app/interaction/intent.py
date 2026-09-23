"""Priority ordered intent routing for VisionCore.

Every deliberate action reaches the control layers through one router, so the
priority order from section 16 of Phase 6 is enforced in one place::

    Input source                     Intent router                Control layer
    ├── Gesture   ─┐
    ├── Interface ─┼──► classify by ActionTier ──► handler ──►  MouseController
    └── Voice *   ─┘                                           DeviceController
         (* reserved, not implemented)

Safety always wins: while an emergency stop is engaged every lower priority
intent is refused, and while control is paused everything below the safety tier
is refused, so a notification can never crowd out a real safety state.

Voice is deliberately absent. No microphone is opened, no speech model is
bundled and no service is contacted anywhere in this project.
:class:`IntentSource.VOICE` exists so a future local layer can plug into exactly
the same router, and it reports ``available = False`` until such a layer
actually exists - it is a reservation in the architecture, not a feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional

from app.interaction.states import ActionTier

logger = logging.getLogger("visioncore.interaction.intent")


class IntentSource(str, Enum):
    """Where an intent came from."""

    GESTURE = "GESTURE"
    INTERFACE = "INTERFACE"
    VOICE = "VOICE"          # reserved: no voice input is implemented

    @property
    def available(self) -> bool:
        """False for sources that have no implementation behind them."""
        return self is not IntentSource.VOICE

    @property
    def label(self) -> str:
        return self.value


class IntentKind(str, Enum):
    """Deliberate actions the application can route."""

    EMERGENCY_STOP = "EMERGENCY_STOP"
    SAFETY_RESET = "SAFETY_RESET"
    CONTROL_TOGGLE = "CONTROL_TOGGLE"
    CONTROL_DISABLE = "CONTROL_DISABLE"
    MODE_MOUSE = "MODE_MOUSE"
    MODE_DEVICE = "MODE_DEVICE"
    DEVICE_ACTION = "DEVICE_ACTION"
    RECOVERY = "RECOVERY"

    @property
    def tier(self) -> ActionTier:
        """Default priority for this kind of intent.

        Disarming control sits at the emergency tier on purpose: releasing the
        pointer and any held button is a safety action, so it must stay
        available while an emergency stop or a pause is active.
        """
        if self in (
            IntentKind.EMERGENCY_STOP,
            IntentKind.SAFETY_RESET,
            IntentKind.CONTROL_DISABLE,
        ):
            return ActionTier.EMERGENCY_STOP
        if self is IntentKind.DEVICE_ACTION:
            return ActionTier.DEVICE_CONTROL
        return ActionTier.SAFETY

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


@dataclass(frozen=True, slots=True)
class Intent:
    """One typed request to act."""

    kind: IntentKind
    source: IntentSource
    tier: ActionTier = ActionTier.SAFETY
    label: str = ""
    payload: Optional[str] = None
    timestamp: float = 0.0

    @classmethod
    def create(
        cls,
        kind: IntentKind,
        source: IntentSource,
        label: str = "",
        payload: Optional[str] = None,
        timestamp: float = 0.0,
    ) -> "Intent":
        """Build an intent using the kind's default priority."""
        return cls(
            kind=kind,
            source=source,
            tier=kind.tier,
            label=label,
            payload=payload,
            timestamp=timestamp,
        )


@dataclass(frozen=True, slots=True)
class IntentOutcome:
    """Result of routing one intent."""

    intent: Intent
    accepted: bool
    handled: bool = False
    reason: str = ""

    @property
    def label(self) -> str:
        return self.intent.label or self.intent.kind.label


Handler = Callable[[Intent], bool]


class IntentRouter:
    """Routes typed intents to the layer that owns them, in priority order."""

    def __init__(self) -> None:
        self._handlers: Dict[IntentKind, Handler] = {}
        self._emergency = False
        self._paused = False
        self.dispatched = 0
        self.suppressed = 0

    # -- wiring ------------------------------------------------------------ #

    def register(self, kind: IntentKind, handler: Handler) -> None:
        """Bind one intent kind to its handler."""
        self._handlers[kind] = handler

    def set_safety(self, emergency: bool = False, paused: bool = False) -> None:
        """Publish the current safety posture of the control layers.

        The application calls this after every control update, so the router
        always knows whether a safety state is active without needing to inspect
        the controllers itself.
        """
        self._emergency = bool(emergency)
        self._paused = bool(paused)

    # -- dispatch ---------------------------------------------------------- #

    def dispatch(self, intent: Intent) -> IntentOutcome:
        """Route one intent, refusing anything a higher priority state outranks."""
        if not intent.source.available:
            self.suppressed += 1
            return IntentOutcome(intent, False, False, "SOURCE UNAVAILABLE")

        tier = intent.tier
        if self._emergency and tier is not ActionTier.EMERGENCY_STOP:
            self.suppressed += 1
            return IntentOutcome(intent, False, False, "EMERGENCY STOP ACTIVE")
        if self._paused and tier > ActionTier.SAFETY:
            self.suppressed += 1
            return IntentOutcome(intent, False, False, "CONTROL PAUSED")

        handler = self._handlers.get(intent.kind)
        if handler is None:
            self.suppressed += 1
            return IntentOutcome(intent, False, False, "NO ROUTE")

        self.dispatched += 1
        try:
            handled = bool(handler(intent))
        except Exception as exc:  # a handler must never take the application down
            logger.warning("Intent %s failed: %s", intent.kind.value, exc)
            return IntentOutcome(intent, True, False, "HANDLER ERROR")
        return IntentOutcome(intent, True, handled, "" if handled else "REFUSED")

    # -- introspection ----------------------------------------------------- #

    @property
    def emergency(self) -> bool:
        return self._emergency

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def routes(self) -> tuple:
        """Registered intent kinds (used by diagnostics)."""
        return tuple(self._handlers)
