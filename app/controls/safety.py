"""Control vocabulary and safety gates for touchless mouse control.

This module holds everything the rest of the application needs in order to know
*what* the control layer may do and *whether* it is allowed to do it right now.
It performs no operating system call itself - that is the job of
:mod:`app.controls.mouse`.

Two ideas are kept strictly separate:

``ControlState``
    Whether device control is enabled at all. ``gesture detected`` never implies
    ``control enabled``: control starts ``DISABLED`` on every launch and only an
    explicit user action can arm it.
``SafetyGate``
    Whether the current tracking evidence is trustworthy enough to act on. A
    failing gate suspends actions without disarming the user's intent, and forces
    any held mouse button to be released.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple

logger = logging.getLogger("visioncore.controls.safety")

Point = Tuple[float, float]

EMPTY_META: Mapping[str, object] = {}


class ControlMode(Enum):
    """Which control layer may act on gestures right now.

    One gesture can never trigger two layers: mouse actions are only performed in
    ``MOUSE`` and device actions only in ``DEVICE``. The mode is changed by an
    explicit interface action, never by a gesture.
    """

    MOUSE = "MOUSE"
    DEVICE = "DEVICE"

    @property
    def label(self) -> str:
        return self.value


class ControlState(Enum):
    """Lifecycle of the operating system control layer."""

    DISABLED = "DISABLED"                # startup / explicitly disabled
    ARMED = "ARMED"                      # enabled, waiting for the pointer pose
    ACTIVE = "ACTIVE"                    # pointer engaged, cursor follows the hand
    PAUSED = "PAUSED"                    # user paused control, tracking continues
    EMERGENCY_STOP = "EMERGENCY_STOP"    # tripped by the stop gesture

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")

    @property
    def allows_actions(self) -> bool:
        """True when this state may perform mouse operations."""
        return self in (ControlState.ARMED, ControlState.ACTIVE)


class ControlAction(Enum):
    """Last mouse operation performed, used for HUD feedback only."""

    NONE = "NONE"
    POINTER_ENGAGED = "POINTER ENGAGED"
    POINTER_RELEASED = "POINTER RELEASED"
    LEFT_CLICK = "LEFT CLICK"
    DRAG_START = "DRAGGING"
    DRAG_END = "DRAG END"
    SCROLL_UP = "SCROLL UP"
    SCROLL_DOWN = "SCROLL DOWN"
    EMERGENCY_STOP = "EMERGENCY STOP"
    RELEASE = "RELEASE"


@dataclass(frozen=True, slots=True)
class ControlSettings:
    """Tunable control and safety parameters (validated on construction)."""

    enabled: bool = True                    # feature available at all
    cursor_smoothing: float = 0.55          # 0 = raw, 1 = heavy
    cursor_speed: float = 1.0               # pointer gain
    cursor_deadzone: float = 0.006          # normalised, prevents jitter
    control_region_margin: float = 0.15     # frame border excluded from control
    click_cooldown_sec: float = 0.45        # minimum gap between two clicks
    drag_hold_sec: float = 0.30             # pinch hold that turns a click into a drag
    scroll_sensitivity: float = 8.0         # wheel notches per normalised unit
    scroll_deadzone: float = 0.015          # normalised, ignores tremor
    scroll_max_notches: int = 6             # clamp for a single frame
    safety_confidence_threshold: float = 0.45
    hand_loss_timeout_sec: float = 0.25     # grace before control state is dropped
    emergency_stop_sec: float = 0.80        # stable open palm before the stop trips
    min_movement_px: float = 1.0            # skip OS calls below this movement

    def clamped(self) -> "ControlSettings":
        """Return a copy with every value forced into a usable range."""
        return ControlSettings(
            enabled=bool(self.enabled),
            cursor_smoothing=_clamp(self.cursor_smoothing, 0.0, 0.95),
            cursor_speed=_clamp(self.cursor_speed, 0.20, 3.0),
            cursor_deadzone=_clamp(self.cursor_deadzone, 0.0, 0.10),
            control_region_margin=_clamp(self.control_region_margin, 0.0, 0.40),
            click_cooldown_sec=_clamp(self.click_cooldown_sec, 0.05, 3.0),
            drag_hold_sec=_clamp(self.drag_hold_sec, 0.15, 2.0),
            scroll_sensitivity=_clamp(self.scroll_sensitivity, 0.5, 25.0),
            scroll_deadzone=_clamp(self.scroll_deadzone, 0.0, 0.20),
            scroll_max_notches=max(1, min(20, int(self.scroll_max_notches))),
            safety_confidence_threshold=_clamp(self.safety_confidence_threshold, 0.05, 0.95),
            hand_loss_timeout_sec=_clamp(self.hand_loss_timeout_sec, 0.05, 2.0),
            emergency_stop_sec=_clamp(self.emergency_stop_sec, 0.20, 5.0),
            min_movement_px=_clamp(self.min_movement_px, 0.0, 20.0),
        )


def _clamp(value: float, low: float, high: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return low
    if numeric != numeric:  # NaN
        return low
    return max(low, min(high, numeric))


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    """Immutable view of the control layer for the HUD and for telemetry."""

    state: ControlState = ControlState.DISABLED
    available: bool = False
    backend: str = "UNSUPPORTED"
    message: str = ""
    pointer_active: bool = False
    dragging: bool = False
    scrolling: bool = False
    suspended: bool = False
    suspended_reason: str = ""
    action: ControlAction = ControlAction.NONE
    action_age: float = 0.0
    cursor: Optional[Point] = None          # index tip in camera coordinates
    screen: Optional[Point] = None          # normalised desktop coordinates
    pixel: Optional[Tuple[int, int]] = None  # desktop pixels
    desktop: Optional[Tuple[int, int]] = None
    clicks: int = 0
    scroll_events: int = 0
    emergency_stops: int = 0

    @property
    def enabled(self) -> bool:
        return self.state in (
            ControlState.ARMED,
            ControlState.ACTIVE,
            ControlState.PAUSED,
            ControlState.EMERGENCY_STOP,
        )


@dataclass(slots=True)
class SafetyVerdict:
    """Outcome of one safety evaluation."""

    healthy: bool = True
    reason: str = ""
    confidence: float = 0.0
    hand_present: bool = False


class SafetyGate:
    """Decides whether tracking evidence is safe to act upon.

    The gate never disarms the user's control state by itself; it only reports
    whether actions are permitted. Callers must release held buttons whenever the
    gate reports a problem.
    """

    def __init__(self, settings: ControlSettings) -> None:
        self.settings = settings
        self._last_healthy: Optional[float] = None
        self._absent_since: Optional[float] = None
        self._last_reason = ""

    def reset(self) -> None:
        self._last_healthy = None
        self._absent_since = None
        self._last_reason = ""

    def evaluate(
        self,
        now: float,
        hand_present: bool,
        confidence: float,
    ) -> SafetyVerdict:
        """Evaluate hand presence and confidence for this frame."""
        threshold = self.settings.safety_confidence_threshold

        if not hand_present:
            self._absent_since = self._absent_since or now
            absent_for = now - self._absent_since
            if absent_for >= self.settings.hand_loss_timeout_sec:
                self._last_reason = "HAND LOST"
                return SafetyVerdict(False, self._last_reason, confidence, False)
            # Short dropouts stay inside the grace window: hold steady instead of
            # acting on stale evidence.
            self._last_reason = "HAND UNSTABLE"
            return SafetyVerdict(False, self._last_reason, confidence, False)

        self._absent_since = None

        if confidence < threshold:
            self._last_reason = "LOW CONFIDENCE"
            return SafetyVerdict(False, self._last_reason, confidence, True)

        self._last_reason = ""
        self._last_healthy = now
        return SafetyVerdict(True, "", confidence, True)

    @property
    def reason(self) -> str:
        return self._last_reason


@dataclass(slots=True)
class ControlCounters:
    """Small mutable tally kept alongside the controller for the HUD."""

    clicks: int = 0
    scroll_events: int = 0
    emergency_stops: int = 0
    meta: Mapping[str, object] = field(default_factory=lambda: EMPTY_META)
