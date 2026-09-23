"""Typed state vocabulary for the VisionCore interaction layer.

Phase 6 gives the interface one consistent, typed way of describing what the
system is doing right now: the interaction lifecycle state, what the central
focus area should say, which status the ring around it carries, how trustworthy
the current tracking evidence is and which priority an action belongs to.

Two rules are enforced by construction:

* Nothing here invents telemetry. Every value is derived from real subsystem
  state (tracking, gestures, mouse control, device control) and the subsystem
  states stay authoritative for behaviour - this vocabulary only describes them.
* Nothing here imports :mod:`app.state`. The application publishes an
  :class:`InteractionSnapshot` into the telemetry model, never the other way
  round, which keeps the interaction layer free of import cycles.

No online service, no model and no microphone is involved anywhere in this
package: the perceived intelligence comes from real state transitions, real
gesture evidence, real action results and contextual presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional


class InteractionLifecycle(str, Enum):
    """Coarse lifecycle reported by the application itself.

    The director never sniffs the application state machine: the application
    declares where it is, so the interaction layer cannot drift out of sync with
    the real lifecycle.
    """

    BOOTING = "BOOTING"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class InteractionState(str, Enum):
    """Headline interaction state shown by the interface.

    This is a *presentation* state: it is resolved every frame from the real
    subsystem states, with safety always winning and short lived events
    (an executed action, a lost hand) taking precedence for as long as they
    genuinely last. The subsystem states remain authoritative for behaviour.
    """

    INITIALIZING = "INITIALIZING"
    SCANNING = "SCANNING"
    HAND_DETECTED = "HAND_DETECTED"
    TRACKING = "TRACKING"
    READY = "READY"
    MOUSE_MODE = "MOUSE_MODE"
    DEVICE_MODE = "DEVICE_MODE"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    HAND_LOST = "HAND_LOST"
    SHUTTING_DOWN = "SHUTTING_DOWN"

    @property
    def label(self) -> str:
        """Human readable form used by the HUD."""
        return self.value.replace("_", " ")

    @property
    def is_safety(self) -> bool:
        """True for states that must never be hidden by a lower priority one."""
        return self in (InteractionState.PAUSED, InteractionState.EMERGENCY_STOP)

    @property
    def is_transient(self) -> bool:
        """True for states that hold for a short window and then release."""
        return self in (InteractionState.ACTION_EXECUTED, InteractionState.HAND_LOST)


class FocusPhase(str, Enum):
    """What the central focus area is currently reporting.

    The order in which these are resolved mirrors the real interaction
    sequence: a safety state first, then a mode change, then the most recent
    real action, then the recognised gesture, then the tracking stage.
    """

    OFFLINE = "OFFLINE"
    SCANNING = "SCANNING"
    ACQUIRING = "ACQUIRING"
    LOCKED = "LOCKED"
    GESTURE = "GESTURE"
    ACTION = "ACTION"
    MODE = "MODE"
    LOST = "LOST"
    PAUSED = "PAUSED"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True, slots=True)
class FocusReadout:
    """Text pair rendered in the central focus area."""

    phase: FocusPhase = FocusPhase.SCANNING
    headline: str = "SCANNING"
    value: str = "NO HAND"


class RingState(str, Enum):
    """Status communicated by the ring around the central focus area.

    Each member maps onto real application state - there is no separate sensor
    or confidence model behind it.
    """

    OFFLINE = "OFFLINE"        # tracking pipeline not running
    SEARCHING = "SEARCHING"    # no hand in view
    TRACKING = "TRACKING"      # hand observed, lock still stabilising
    READY = "READY"            # locked and armed, waiting for a gesture
    ACTIVE = "ACTIVE"          # a control layer is acting right now
    PAUSED = "PAUSED"          # user paused control
    EMERGENCY = "EMERGENCY"    # safety stop tripped


class TrackingQuality(str, Enum):
    """Real tracking condition, derived from tracker state and confidence."""

    OFFLINE = "OFFLINE"
    SEARCHING = "SEARCHING"
    ACQUIRING = "ACQUIRING"
    LOCKED = "LOCKED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    LOST = "LOST"

    @property
    def label(self) -> str:
        """Short status word shown next to ``TRACKING`` in the HUD."""
        if self is TrackingQuality.LOW_CONFIDENCE:
            return "LOW CONFIDENCE"
        if self is TrackingQuality.OFFLINE:
            return "UNAVAILABLE"
        if self is TrackingQuality.LOST:
            return "HAND LOST"
        return self.value


class ActionTier(IntEnum):
    """Action priority (Phase 6, section 16).

    Lower is more important. A visual effect can never take precedence over a
    safety state, and the intent router refuses lower priority invitations while
    a higher priority state is active.
    """

    EMERGENCY_STOP = 0
    SAFETY = 1
    MOUSE_CONTROL = 2
    DEVICE_CONTROL = 3
    VISUAL_FEEDBACK = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ")


class RecoveryAction(str, Enum):
    """A recovery the application can genuinely attempt.

    Only actions with a real implementation are ever offered by the interface;
    the label is what the HUD shows on the button.
    """

    CAMERA = "CAMERA"
    TRACKING = "TRACKING"
    CONTROL = "CONTROL"

    @property
    def label(self) -> str:
        return "RETRY"


@dataclass(frozen=True, slots=True)
class SystemError:
    """An error condition presented as a readable HUD state.

    ``detail`` carries the real message from the failing subsystem and
    ``recovery`` is only set when the application can actually retry.
    """

    title: str
    detail: str = ""
    recovery: Optional[RecoveryAction] = None

    @property
    def recovery_label(self) -> Optional[str]:
        return self.recovery.label if self.recovery is not None else None


@dataclass(frozen=True, slots=True)
class InteractionSnapshot:
    """Immutable view of the interaction layer for one frame."""

    state: InteractionState = InteractionState.INITIALIZING
    previous_state: InteractionState = InteractionState.INITIALIZING
    state_age: float = 0.0
    transition: float = 0.0
    focus: FocusReadout = FocusReadout()
    ring: RingState = RingState.SEARCHING
    ring_activity: float = 0.0
    ring_progress: float = 0.0
    ring_phase: float = 0.0
    quality: TrackingQuality = TrackingQuality.SEARCHING
    confidence: Optional[float] = None
    hands: int = 0
    max_hands: int = 1
    mode_label: str = "MOUSE"
    mode_transition: float = 1.0
    error: Optional[SystemError] = None
    now: float = 0.0

    @property
    def transitioning(self) -> bool:
        """True while the state change is still animating in."""
        return self.transition < 1.0
