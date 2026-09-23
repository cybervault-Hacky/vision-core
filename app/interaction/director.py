"""Context aware interaction director for VisionCore.

The director is the one place that turns real subsystem state into the single
interaction state the interface shows. It reads published telemetry only
(tracking state, gesture state, control states, action counters, capability
reports) and never touches a controller, the camera or the operating system, so
it cannot alter behaviour - it can only describe it.

Responsibilities:

* resolve the headline :class:`InteractionState` with a strict priority order,
  safety first, transient events second;
* keep the transition timings that make state changes read as transitions
  instead of flicker;
* derive the central focus readout, the status ring and the tracking quality
  word from measured values;
* emit real action feedback and the in-memory recent-action timeline.

Timing model: the director is driven by the application frame loop with a
monotonic timestamp, so every animation is frame-rate independent and can be
validated deterministically.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from app.controls.device_types import DeviceAction
from app.controls.safety import ControlAction, ControlMode, ControlState
from app.gestures.types import Gesture, GesturePhase, GestureState
from app.hand_tracking import TrackingState
from app.interaction.feedback import ActionFeedback, FeedbackCenter, FeedbackSource
from app.interaction.states import (
    ActionTier,
    FocusPhase,
    FocusReadout,
    InteractionLifecycle,
    InteractionSnapshot,
    InteractionState,
    RecoveryAction,
    RingState,
    SystemError,
    TrackingQuality,
)

if TYPE_CHECKING:  # avoids an import cycle: app.state imports the vocabulary above
    from app.state import Telemetry

logger = logging.getLogger("visioncore.interaction")

# Transition timings (seconds).
STATE_TRANSITION_SEC = 0.28
ACTION_HOLD_SEC = 1.10
LOST_HOLD_SEC = 0.90
MODE_TRANSITION_SEC = 0.55

# Ring sweep speed per state, in turns per second. States with more work in
# flight sweep faster; a stopped pipeline does not sweep at all.
_RING_SWEEP: Dict[RingState, float] = {
    RingState.OFFLINE: 0.0,
    RingState.SEARCHING: 0.075,
    RingState.TRACKING: 0.24,
    RingState.READY: 0.13,
    RingState.ACTIVE: 0.36,
    RingState.PAUSED: 0.05,
    RingState.EMERGENCY: 0.55,
}

# Resting brightness of the ring per state (0..1), modulated by the pulse.
_RING_ACTIVITY: Dict[RingState, float] = {
    RingState.OFFLINE: 0.0,
    RingState.SEARCHING: 0.30,
    RingState.TRACKING: 0.62,
    RingState.READY: 0.78,
    RingState.ACTIVE: 0.95,
    RingState.PAUSED: 0.55,
    RingState.EMERGENCY: 1.0,
}

_PULSE_RATE = 0.55

# Mouse actions worth reporting. Pointer engage/release are state, not actions,
# so they never produce a notification.
_MOUSE_FEEDBACK: Dict[ControlAction, str] = {
    ControlAction.LEFT_CLICK: "LEFT CLICK",
    ControlAction.DRAG_START: "DRAG START",
    ControlAction.DRAG_END: "DRAG END",
    ControlAction.SCROLL_UP: "SCROLL UP",
    ControlAction.SCROLL_DOWN: "SCROLL DOWN",
    ControlAction.EMERGENCY_STOP: "EMERGENCY STOP",
}

# Readable forms for device results whose typed label is terse.
_DEVICE_FEEDBACK: Dict[str, str] = {
    "NEXT WINDOW": "WINDOW SWITCH",
    "LAUNCH APP": "APP LAUNCHED",
    "MINIMIZE": "WINDOW MINIMIZE",
    "MAXIMIZE": "WINDOW MAXIMIZE",
}

_FOCUS_SCANNING = FocusReadout(FocusPhase.SCANNING, "SCANNING", "NO HAND")
_FOCUS_OFFLINE = FocusReadout(FocusPhase.OFFLINE, "TRACKING", "UNAVAILABLE")
_FOCUS_LOCKED = FocusReadout(FocusPhase.LOCKED, "TRACKING", "LOCKED")
_FOCUS_ACQUIRING = FocusReadout(FocusPhase.ACQUIRING, "HAND DETECTED", "ACQUIRING")
_FOCUS_LOST = FocusReadout(FocusPhase.LOST, "TRACKING", "LOST")
_FOCUS_PAUSED = FocusReadout(FocusPhase.PAUSED, "CONTROL", "PAUSED")

# Severity order for reported errors: the first active one is displayed.
_ERROR_ORDER: Tuple[str, ...] = (
    RecoveryAction.CAMERA.value,
    RecoveryAction.TRACKING.value,
    RecoveryAction.CONTROL.value,
)


class InteractionDirector:
    """Derives the interaction state, focus readout and action feedback."""

    def __init__(self, confidence_threshold: float = 0.45, max_hands: int = 1) -> None:
        self.confidence_threshold = float(confidence_threshold)
        self.max_hands = max(1, int(max_hands))

        self.feedback = FeedbackCenter()

        self._lifecycle = InteractionLifecycle.BOOTING
        self._tracking_available = True

        self._state = InteractionState.INITIALIZING
        self._previous_state = InteractionState.INITIALIZING
        self._state_changed_at = 0.0

        self._action_at: Optional[float] = None
        self._action_label = ""

        self._previous_tracking = TrackingState.NO_HAND
        self._lost_at: Optional[float] = None

        self._mode_label = ControlMode.MOUSE.label
        self._mode_changed_at: Optional[float] = None
        self._mode_transition = 1.0

        self._ring_phase = 0.0
        self._pulse = 0.0

        self._errors: Dict[str, SystemError] = {}

        self._mouse_events = 0
        self._device_events = 0
        self._mouse_state = ControlState.DISABLED
        self._device_state = ControlState.DISABLED
        self._device_message = ""
        self._emergency_active = False

        self._focus_cache = _FOCUS_SCANNING
        self._focus_key: Tuple[FocusPhase, str, str] = (
            FocusPhase.SCANNING,
            "SCANNING",
            "NO HAND",
        )
        self._snapshot = InteractionSnapshot()
        self._primed = False

    # -- application wiring ------------------------------------------------ #

    def set_lifecycle(self, lifecycle: InteractionLifecycle) -> None:
        """Declare the application lifecycle (never sniffed from the outside)."""
        self._lifecycle = lifecycle

    def set_tracking_available(self, available: bool) -> None:
        """Declare whether a tracking pipeline is actually running."""
        self._tracking_available = bool(available)

    def report_error(self, error: SystemError) -> None:
        """Register an error state; an optional recovery must really exist."""
        key = error.recovery.value if error.recovery is not None else error.title
        current = self._errors.get(key)
        if current is not None and current.title == error.title:
            return
        self._errors[key] = error
        logger.warning(
            "Interaction error state: %s (%s)", error.title, error.detail or "no detail"
        )
        self.feedback.record(
            error.title,
            FeedbackSource.SYSTEM,
            ActionTier.SAFETY,
            success=False,
            detail=error.detail,
            now=time.perf_counter(),
        )

    def clear_error(self, recovery: Optional[RecoveryAction] = None, title: str = "") -> None:
        """Clear a reported error state (recovered, or the condition is gone)."""
        if recovery is not None:
            self._errors.pop(recovery.value, None)
        if title:
            for key in [key for key, value in self._errors.items() if value.title == title]:
                self._errors.pop(key, None)

    def notify(self, label: str, success: bool = True, detail: str = "") -> Optional[ActionFeedback]:
        """Record a visual notification for a real outcome.

        Visual feedback is the lowest priority in the action order, so while an
        emergency stop owns the notification nothing is recorded at all: the
        safety message must not be pushed aside by a status line.
        """
        if self._emergency_active:
            logger.debug("Notification suppressed during emergency stop: %s", label)
            return None
        return self.feedback.record(
            label,
            FeedbackSource.SYSTEM,
            ActionTier.VISUAL_FEEDBACK,
            success=success,
            detail=detail,
            now=time.perf_counter(),
        )

    @property
    def error(self) -> Optional[SystemError]:
        """Highest severity active error: camera, then tracking, then control."""
        for key in _ERROR_ORDER:
            error = self._errors.get(key)
            if error is not None:
                return error
        for error in self._errors.values():
            return error
        return None

    # -- per frame --------------------------------------------------------- #

    def update(
        self,
        dt: float,
        telemetry: "Telemetry",
        now: Optional[float] = None,
    ) -> InteractionSnapshot:
        """Advance the interaction layer by one frame and publish it."""
        now = time.perf_counter() if now is None else now
        dt = max(0.0, min(0.25, float(dt)))
        self._pulse = (self._pulse + dt * _PULSE_RATE) % 1.0

        if not self._primed:
            self._prime(telemetry)

        self._observe_actions(telemetry, now)
        self._observe_safety(telemetry, now)
        self._observe_device_notices(telemetry, now)
        self._observe_tracking(telemetry, now)
        self._observe_mode(telemetry, now)

        ring = self._ring(telemetry)
        self._ring_phase = (self._ring_phase + dt * _RING_SWEEP[ring]) % 1.0

        state = self._resolve_state(telemetry, now)
        if state is not self._state:
            self._previous_state = self._state
            self._state = state
            self._state_changed_at = now
            logger.debug(
                "Interaction state %s -> %s", self._previous_state.value, state.value
            )

        snapshot = InteractionSnapshot(
            state=self._state,
            previous_state=self._previous_state,
            state_age=max(0.0, now - self._state_changed_at),
            transition=min(
                1.0, max(0.0, now - self._state_changed_at) / STATE_TRANSITION_SEC
            ),
            focus=self._focus_readout(telemetry, now),
            ring=ring,
            ring_activity=_RING_ACTIVITY[ring],
            ring_progress=self._ring_progress(telemetry),
            ring_phase=self._ring_phase,
            quality=self._quality(telemetry),
            confidence=telemetry.hand_confidence,
            hands=telemetry.hands_detected,
            max_hands=self.max_hands,
            mode_label=self._mode_label,
            mode_transition=self._mode_transition,
            error=self.error,
            now=now,
        )
        self._snapshot = snapshot

        telemetry.update_interaction(
            snapshot,
            self.feedback.timeline,
            self.feedback.toast(now),
        )
        return snapshot

    @property
    def snapshot(self) -> InteractionSnapshot:
        """Most recently published snapshot."""
        return self._snapshot

    @property
    def state(self) -> InteractionState:
        return self._state

    @property
    def pulse(self) -> float:
        """Shared oscillator in ``[0, 1)`` so every widget breathes together."""
        return self._pulse

    # -- observation ------------------------------------------------------- #

    def _prime(self, telemetry: "Telemetry") -> None:
        """Adopt the current counters without reporting them as new events."""
        self._mouse_events = telemetry.control_action_events
        self._device_events = telemetry.device_action_events
        self._mouse_state = telemetry.control_state
        self._device_state = telemetry.device_state
        self._device_message = telemetry.device_message
        self._mode_label = telemetry.control_mode.label
        self._primed = True

    def _observe_actions(self, telemetry: "Telemetry", now: float) -> None:
        """Report real, completed actions - never an intention or a pending call."""
        if telemetry.control_action_events != self._mouse_events:
            self._mouse_events = telemetry.control_action_events
            action = telemetry.control_action
            label = _MOUSE_FEEDBACK.get(action)
            if label is not None:
                stopped = action is ControlAction.EMERGENCY_STOP
                self.feedback.record(
                    label,
                    FeedbackSource.MOUSE,
                    ActionTier.EMERGENCY_STOP if stopped else ActionTier.MOUSE_CONTROL,
                    success=not stopped,
                    now=now,
                )
                if not stopped:
                    self._note_action(label, now)

        if telemetry.device_action_events != self._device_events:
            self._device_events = telemetry.device_action_events
            raw = telemetry.device_action_label
            if raw:
                success = telemetry.device_action_success
                label = _DEVICE_FEEDBACK.get(raw, raw)
                self.feedback.record(
                    label,
                    FeedbackSource.DEVICE,
                    ActionTier.DEVICE_CONTROL,
                    success=success,
                    detail=self._device_detail(telemetry),
                    now=now,
                )
                if success:
                    self._note_action(label, now)

    def _observe_safety(self, telemetry: "Telemetry", now: float) -> None:
        """Safety transitions are reported once, in priority order."""
        mouse_state = telemetry.control_state
        device_state = telemetry.device_state
        emergency = (
            mouse_state is ControlState.EMERGENCY_STOP
            or device_state is ControlState.EMERGENCY_STOP
        )

        if emergency and not self._emergency_active:
            self._emergency_active = True
            reason = telemetry.control_message or telemetry.device_message or "SAFETY STOP"
            entry = self.feedback.record(
                "EMERGENCY STOP",
                FeedbackSource.SAFETY,
                ActionTier.EMERGENCY_STOP,
                True,
                reason,
                now,
                sticky=True,
            )
            self.feedback.set_sticky(entry)
        elif not emergency and self._emergency_active:
            self._emergency_active = False
            self.feedback.clear_sticky()
            self.feedback.record(
                "EMERGENCY CLEARED",
                FeedbackSource.SAFETY,
                ActionTier.SAFETY,
                True,
                "",
                now,
            )

        self._observe_layer("MOUSE", mouse_state, self._mouse_state, now)
        self._observe_layer("DEVICE", device_state, self._device_state, now)
        self._mouse_state = mouse_state
        self._device_state = device_state

    def _observe_layer(
        self,
        name: str,
        state: ControlState,
        previous: ControlState,
        now: float,
    ) -> None:
        """Report enable, pause, resume and disarm transitions of one layer."""
        if state is previous:
            return
        if state is ControlState.PAUSED:
            self.feedback.record(
                "CONTROL PAUSED", FeedbackSource.SAFETY, ActionTier.SAFETY, True, name, now
            )
        elif previous is ControlState.PAUSED and state.allows_actions:
            self.feedback.record(
                "CONTROL RESUMED", FeedbackSource.SAFETY, ActionTier.SAFETY, True, name, now
            )
        elif previous is ControlState.DISABLED and state.allows_actions:
            self.feedback.record(
                "CONTROL ENABLED", FeedbackSource.SAFETY, ActionTier.SAFETY, True, name, now
            )
        elif state is ControlState.DISABLED and previous is not ControlState.DISABLED:
            self.feedback.record(
                "CONTROL DISARMED", FeedbackSource.SAFETY, ActionTier.SAFETY, True, name, now
            )

    def _observe_device_notices(self, telemetry: "Telemetry", now: float) -> None:
        """Surface capability refusals reported by the device layer, once each."""
        message = telemetry.device_message
        if not message or message == self._device_message:
            return
        self._device_message = message
        upper = message.upper()
        if "UNAVAILABLE" not in upper and "NOT SUPPORTED" not in upper:
            return
        words = message.split()
        capability = words[0] if words else ""
        self.feedback.record(
            message,
            FeedbackSource.DEVICE,
            ActionTier.SAFETY,
            success=False,
            detail=telemetry.device_capability_notes.get(capability, ""),
            now=now,
        )

    def _observe_tracking(self, telemetry: "Telemetry", now: float) -> None:
        """Register the moment a tracked hand disappears.

        The tracker owns the decision (it declares ``HAND_LOST`` after its own
        grace window); the director only remembers when that happened so the
        interface can show a short, animated loss transition. Safety behaviour is
        not involved: the control layers react to the same tracker state in the
        very same frame.
        """
        state = telemetry.tracking_state
        if self._previous_tracking.is_engaged and not state.is_engaged:
            self._lost_at = now
        elif state.is_engaged:
            self._lost_at = None
        self._previous_tracking = state

    def _observe_mode(self, telemetry: "Telemetry", now: float) -> None:
        """Announce a control mode change and animate the transition."""
        label = telemetry.control_mode.label
        if label != self._mode_label:
            self._mode_label = label
            self._mode_changed_at = now
            self._mode_transition = 0.0
            self.feedback.record(
                f"{label} CONTROL ACTIVE",
                FeedbackSource.MODE,
                ActionTier.SAFETY,
                True,
                "",
                now,
            )
            logger.info("Interaction mode transition to %s", label)

        if self._mode_changed_at is None:
            self._mode_transition = 1.0
            return
        progress = (now - self._mode_changed_at) / MODE_TRANSITION_SEC
        if progress >= 1.0:
            self._mode_changed_at = None
            self._mode_transition = 1.0
        else:
            self._mode_transition = max(0.0, progress)

    # -- derivation -------------------------------------------------------- #

    def _note_action(self, label: str, now: float) -> None:
        """Remember the last action that really executed, for the focus area."""
        self._action_at = now
        self._action_label = label

    def _resolve_state(self, telemetry: "Telemetry", now: float) -> InteractionState:
        """Resolve the headline state: safety first, then events, then activity."""
        lifecycle = self._lifecycle
        if lifecycle is InteractionLifecycle.BOOTING:
            return InteractionState.INITIALIZING
        if lifecycle is InteractionLifecycle.SHUTTING_DOWN:
            return InteractionState.SHUTTING_DOWN

        mouse_state = telemetry.control_state
        device_state = telemetry.device_state
        if (
            mouse_state is ControlState.EMERGENCY_STOP
            or device_state is ControlState.EMERGENCY_STOP
        ):
            return InteractionState.EMERGENCY_STOP
        if mouse_state is ControlState.PAUSED or device_state is ControlState.PAUSED:
            return InteractionState.PAUSED

        if self._lost_at is not None and now - self._lost_at <= LOST_HOLD_SEC:
            return InteractionState.HAND_LOST
        if self._action_at is not None and now - self._action_at <= ACTION_HOLD_SEC:
            return InteractionState.ACTION_EXECUTED

        return self._base_state(telemetry)

    def _base_state(self, telemetry: "Telemetry") -> InteractionState:
        """State implied by the subsystems when nothing transient is happening."""
        mouse_state = telemetry.control_state
        device_state = telemetry.device_state
        tracking = telemetry.tracking_state

        if tracking is TrackingState.DETECTING:
            return InteractionState.HAND_DETECTED
        if tracking is TrackingState.TRACKING:
            if mouse_state is ControlState.ACTIVE or device_state is ControlState.ACTIVE:
                if self._mode_label == ControlMode.DEVICE.label:
                    return InteractionState.DEVICE_MODE
                return InteractionState.MOUSE_MODE
            if mouse_state.allows_actions or device_state.allows_actions:
                return InteractionState.READY
            return InteractionState.TRACKING
        return InteractionState.SCANNING

    def _focus_readout(self, telemetry: "Telemetry", now: float) -> FocusReadout:
        """Central readout: safety, mode change, action, gesture, tracking stage."""
        if (
            telemetry.control_state is ControlState.EMERGENCY_STOP
            or telemetry.device_state is ControlState.EMERGENCY_STOP
        ):
            reason = telemetry.control_message or telemetry.device_message or "SAFETY STOP"
            return self._cached_focus(FocusPhase.EMERGENCY, "EMERGENCY STOP", reason)
        if (
            telemetry.control_state is ControlState.PAUSED
            or telemetry.device_state is ControlState.PAUSED
        ):
            return _FOCUS_PAUSED
        if self._mode_transition < 1.0:
            return self._cached_focus(
                FocusPhase.MODE, "CONTROL MODE", f"{self._mode_label} MODE"
            )
        if self._action_at is not None and now - self._action_at <= ACTION_HOLD_SEC:
            return self._cached_focus(FocusPhase.ACTION, "ACTION", self._action_label)

        gesture = telemetry.gesture
        if gesture is not Gesture.NONE and (
            telemetry.gesture_state is GestureState.RECOGNIZED
            or telemetry.gesture_phase is GesturePhase.RELEASE
        ):
            return self._cached_focus(FocusPhase.GESTURE, "GESTURE", gesture.label)

        if not self._tracking_available:
            return _FOCUS_OFFLINE

        tracking = telemetry.tracking_state
        if tracking is TrackingState.TRACKING:
            return _FOCUS_LOCKED
        if tracking is TrackingState.DETECTING:
            return _FOCUS_ACQUIRING
        if tracking is TrackingState.HAND_LOST:
            return _FOCUS_LOST
        return _FOCUS_SCANNING

    def _cached_focus(self, phase: FocusPhase, headline: str, value: str) -> FocusReadout:
        """Build a readout only when its text actually changes."""
        key = (phase, headline, value)
        if key != self._focus_key:
            self._focus_key = key
            self._focus_cache = FocusReadout(phase, headline, value)
        return self._focus_cache

    def _ring(self, telemetry: "Telemetry") -> RingState:
        """Ring status, mapped onto real application state only."""
        if self._lifecycle is InteractionLifecycle.SHUTTING_DOWN:
            return RingState.OFFLINE
        if not self._tracking_available:
            return RingState.OFFLINE

        mouse_state = telemetry.control_state
        device_state = telemetry.device_state
        if (
            mouse_state is ControlState.EMERGENCY_STOP
            or device_state is ControlState.EMERGENCY_STOP
        ):
            return RingState.EMERGENCY
        if mouse_state is ControlState.PAUSED or device_state is ControlState.PAUSED:
            return RingState.PAUSED
        if mouse_state is ControlState.ACTIVE or device_state is ControlState.ACTIVE:
            return RingState.ACTIVE

        tracking = telemetry.tracking_state
        if tracking is TrackingState.TRACKING:
            return RingState.READY
        if tracking.is_engaged:
            return RingState.TRACKING
        return RingState.SEARCHING

    def _ring_progress(self, telemetry: "Telemetry") -> float:
        """Real acquisition progress: measured lock frames, or full lock."""
        tracking = telemetry.tracking_state
        if tracking is TrackingState.DETECTING:
            return max(0.0, min(1.0, telemetry.tracking_lock_progress))
        if tracking is TrackingState.TRACKING:
            return 1.0
        return 0.0

    def _quality(self, telemetry: "Telemetry") -> TrackingQuality:
        """Tracking quality word, from tracker state and measured confidence."""
        if not self._tracking_available:
            return TrackingQuality.OFFLINE
        state = telemetry.tracking_state
        if state is TrackingState.HAND_LOST:
            return TrackingQuality.LOST
        if state is TrackingState.DETECTING:
            return TrackingQuality.ACQUIRING
        if state is TrackingState.TRACKING:
            confidence = telemetry.hand_confidence
            if confidence is not None and confidence < self.confidence_threshold:
                return TrackingQuality.LOW_CONFIDENCE
            return TrackingQuality.LOCKED
        return TrackingQuality.SEARCHING

    # -- helpers ----------------------------------------------------------- #

    def _device_detail(self, telemetry: "Telemetry") -> str:
        """Real supporting detail for a device notification, when available."""
        action = telemetry.device_action
        if action is not None:
            if (
                action
                in (
                    DeviceAction.VOLUME_UP,
                    DeviceAction.VOLUME_DOWN,
                    DeviceAction.MUTE,
                )
                and telemetry.device_volume_known
                and telemetry.device_volume is not None
            ):
                return f"{telemetry.device_volume * 100:.0f}%"
            if (
                action in (DeviceAction.BRIGHTNESS_UP, DeviceAction.BRIGHTNESS_DOWN)
                and telemetry.device_brightness_known
                and telemetry.device_brightness is not None
            ):
                return f"{telemetry.device_brightness * 100:.0f}%"
        return telemetry.device_action_detail

    def timeline(self) -> Tuple[ActionFeedback, ...]:
        """Recent actions, newest first (memory only)."""
        return self.feedback.timeline
