"""Touchless device controller: gestures to explicit, safe operating system actions.

The device layer sits beside the mouse layer, never inside it:

    Gesture engine  ->  DeviceController  ->  DeviceBackend  ->  OS API

and it only acts while the control mode is ``DEVICE``. Nothing here recognises a
gesture: the controller consumes the gesture result and the tracking confidence
that the layers before it produced.

Policy enforced by this module
------------------------------
* Nothing happens unless the user has explicitly enabled device control and the
  control mode is ``DEVICE``.
* Every action passes a fixed chain: mode -> enabled -> safety gate (hand present
  and confidence above the threshold) -> gesture stability -> cooldown ->
  platform capability.
* Event actions (mute, play/pause, track skip) fire once per gesture cycle and
  respect an action cooldown; continuous actions (volume, brightness) are travel
  driven, rate limited and clamped, and stop the instant the gesture, the hand or
  the confidence goes away.
* ``OPEN_PALM`` is play/pause when briefly held and the emergency stop when held
  clearly longer, so the Phase 4 stop gesture stays reliable while media control
  remains usable.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

from app.controls.device_backend import DeviceBackend, create_device_backend
from app.controls.device_types import (
    CapabilityReport,
    DeviceAction,
    DeviceActionResult,
    DeviceCapability,
    DeviceSettings,
    DeviceSnapshot,
    MediaAction,
    WindowAction,
)
from app.controls.safety import ControlMode, ControlSettings, ControlState, SafetyGate
from app.gestures import Gesture, GesturePhase, GestureSnapshot
from app.hand_tracking import FINGER_JOINTS, Hand, TrackingSnapshot

logger = logging.getLogger("visioncore.controls.device")

# Stable palm anchors used for continuous travel (wrist and middle knuckle).
_TRAVEL_ANCHORS = (0, FINGER_JOINTS["MIDDLE"][0])

# Minimum gap between two identical "capability unavailable" notices.
_NOTE_INTERVAL_SEC = 2.5

# Volume/brightness change applied by one direct (non-gesture) request, in percent.
DIRECT_STEP = 6.0


class DeviceController:
    """Routes stable gestures to platform device actions, safely."""

    def __init__(
        self,
        settings: Optional[DeviceSettings] = None,
        backend: Optional[DeviceBackend] = None,
        gate_settings: Optional[ControlSettings] = None,
    ) -> None:
        self.settings = (settings or DeviceSettings()).clamped()
        self.backend = backend if backend is not None else create_device_backend()
        # The device layer reuses the mouse control safety gate: same confidence
        # threshold, same hand loss grace, so both layers trust the same evidence.
        self._gate = SafetyGate(
            (gate_settings or ControlSettings(enabled=self.settings.enabled)).clamped()
        )

        self._state = ControlState.DISABLED
        self._mode = ControlMode.MOUSE
        self._message = ""
        self._suspended = False
        self._suspended_reason = ""

        self._open_palm_since: Optional[float] = None
        self._mute_cycle_active = False
        self._last_action_at: Dict[DeviceAction, float] = {}
        self._last_result: Optional[DeviceActionResult] = None

        self._volume: Optional[float] = None
        self._volume_steps = 0
        self._muted: Optional[bool] = None
        self._brightness: Optional[float] = None
        self._volume_travel = 0.0
        self._brightness_travel = 0.0
        # One previous-frame anchor per continuous control: the volume and
        # brightness handlers must never reset each other's travel.
        self._travel_anchors: Dict[str, Optional[float]] = {}
        self._last_volume_action_at = 0.0
        self._last_brightness_action_at = 0.0
        self._level_read_at = 0.0
        self._actions = 0
        self._action_events = 0
        self._emergency_stops = 0
        self._notes: Dict[DeviceCapability, float] = {}
        self._frame_now: Optional[float] = None

        self._snapshot = self._build_snapshot(now=0.0, action_age=0.0)

    # -- introspection ----------------------------------------------------- #

    @property
    def state(self) -> ControlState:
        return self._state

    @property
    def mode(self) -> ControlMode:
        return self._mode

    @property
    def available(self) -> bool:
        return bool(self.settings.enabled and self.backend.available)

    @property
    def message(self) -> str:
        return self._message

    @property
    def snapshot(self) -> DeviceSnapshot:
        return self._snapshot

    def capabilities(self) -> Tuple[CapabilityReport, ...]:
        return self.backend.capabilities()

    def supports(self, capability: DeviceCapability) -> bool:
        return self.backend.supports(capability)

    def _startup_message(self) -> str:
        if not self.settings.enabled:
            return "DISABLED IN CONFIGURATION"
        if not self.backend.available:
            return self.backend.reason
        return ""

    # -- user commands ----------------------------------------------------- #

    def set_mode(self, mode: ControlMode) -> None:
        """Adopt the application control mode; leaving DEVICE cancels everything."""
        if mode is self._mode:
            return
        self._mode = mode
        if mode is not ControlMode.DEVICE:
            self._cancel_continuous()
            self._open_palm_since = None
            self._mute_cycle_active = False
            if self._state is ControlState.ACTIVE:
                # Leaving DEVICE mode suspends the layer: it stays armed but can
                # perform nothing, so neither the HUD nor telemetry may report it
                # as ACTIVE while the application is in MOUSE mode. The emergency
                # stop, a pause and a disabled layer are all left untouched.
                self._state = ControlState.ARMED
        logger.info("Device control mode: %s", mode.label)

    def enable(self) -> bool:
        """Arm device control. Returns False (with a reason) when impossible."""
        if not self.settings.enabled:
            self._message = "DISABLED IN CONFIGURATION"
            logger.info("Device control cannot be enabled: %s", self._message)
            return False
        if not self.backend.available:
            self._message = self.backend.reason
            logger.warning("Device control unavailable: %s", self.backend.detail)
            return False

        self._gate.reset()
        self._state = ControlState.ARMED
        self._message = ""
        self._refresh_levels(force=True)
        # Publish immediately: the layer is armed for work now, and every reader
        # (telemetry, the assistant's context, the HUD) must see that now.
        self._publish(self._now())
        logger.info("Device control armed (backend=%s)", self.backend.name)
        return True

    def disable(self) -> None:
        """Disarm device control and clear every pending action."""
        self._cancel_continuous()
        self._state = ControlState.DISABLED
        self._message = self._startup_message()
        self._publish(self._now())
        logger.info("Device control disabled")

    def pause(self) -> None:
        """Suspend actions while tracking continues."""
        if self._state in (ControlState.ARMED, ControlState.ACTIVE):
            self._cancel_continuous()
            self._state = ControlState.PAUSED
            self._message = ""
            self._publish(self._now())
            logger.info("Device control paused")

    def resume(self) -> None:
        """Return to ARMED from PAUSED or after an emergency stop."""
        if self._state not in (ControlState.PAUSED, ControlState.EMERGENCY_STOP):
            return
        if not self.available:
            self.enable()
            return
        self._gate.reset()
        self._state = ControlState.ARMED
        self._message = ""
        self._publish(self._now())
        logger.info("Device control resumed")

    def toggle(self) -> bool:
        """Primary UI action: enable, pause or resume depending on state."""
        if self._state is ControlState.DISABLED:
            return self.enable()
        if self._state in (ControlState.ARMED, ControlState.ACTIVE):
            self.pause()
        else:
            self.resume()
        return True

    def emergency_stop(self, reason: str = "EMERGENCY STOP") -> None:
        """Cancel every pending device action and latch the stop."""
        if self._state in (ControlState.DISABLED, ControlState.EMERGENCY_STOP):
            return
        self._cancel_continuous()
        # Clear every pending cycle and cooldown so a deliberate resume starts
        # from a clean slate instead of inheriting the stopped gesture's timing.
        self._open_palm_since = None
        self._mute_cycle_active = False
        self._last_action_at.clear()
        self._state = ControlState.EMERGENCY_STOP
        self._emergency_stops += 1
        self._message = reason
        self._record(
            DeviceActionResult(
                success=True,
                action=None,
                message="EMERGENCY STOP",
                timestamp=self._now(),
                detail=reason,
            )
        )
        logger.warning("Device emergency stop: %s", reason)
        self._publish(self._now())

    def on_tracking_lost(self) -> None:
        """Tracking pipeline stopped (camera loss): cancel, never keep a state."""
        self._cancel_continuous()
        self._gate.reset()
        if self._state is ControlState.ACTIVE:
            self._state = ControlState.ARMED

    def close(self) -> None:
        """Fail-safe teardown."""
        self._cancel_continuous()
        self._state = ControlState.DISABLED
        try:
            self.backend.close()
        except Exception as exc:
            logger.warning("Error closing device backend: %s", exc)

    # -- direct actions (interface buttons) -------------------------------- #

    def perform_window_action(self, action: WindowAction) -> DeviceActionResult:
        """Window action requested from the interface (never from a gesture)."""
        mapped = {
            WindowAction.MINIMIZE: DeviceAction.MINIMIZE,
            WindowAction.MAXIMIZE: DeviceAction.MAXIMIZE,
            WindowAction.NEXT: DeviceAction.NEXT_WINDOW,
        }[action]
        return self.perform(mapped, runner=lambda: self.backend.window(action))

    def perform_launch(self, key: str) -> DeviceActionResult:
        """Launch an allowlisted application requested from the interface."""
        label = dict(self.backend.launchable()).get(key, key.upper())
        return self.perform(
            DeviceAction.LAUNCH_APP,
            runner=lambda: self.backend.launch(key),
            detail=label,
        )

    def perform(self, action: DeviceAction, runner=None, detail: str = "") -> DeviceActionResult:
        """Run one action through the full gate chain (also used by buttons).

        The rate policy is applied here exactly as the gesture handlers apply it,
        so a direct request - VisionCore AI or an interface button - can neither
        repeat an action faster than its cooldown nor fire the same action a
        gesture just performed. Continuous actions (volume, brightness) use their
        own interval, event actions (media, mute, window) use the cooldown.
        """
        now = self._now()
        capability = action.capability
        if action.is_continuous:
            interval, last = self._continuous_policy(action)
            if not self._rate_ok(now, last, interval):
                return self._rate_limited(action, now, capability, interval)
        else:
            last = self._last_action_at.get(action)
            if last is not None and not self._rate_ok(
                now, last, self.settings.action_cooldown_sec
            ):
                return self._rate_limited(
                    action, now, capability, self.settings.action_cooldown_sec
                )

        result = self._execute(action, now, runner, capability, detail)
        if result.success:
            if action.is_continuous:
                self._note_continuous_action(action, now)
            else:
                self._last_action_at[action] = now
        self._record(result)
        # Direct actions happen outside the frame loop, so republish at once:
        # the interface must show this result, never the previous one.
        self._publish(now)
        return result

    def _continuous_policy(self, action: DeviceAction) -> Tuple[float, float]:
        """Rate interval and last-action time for a continuous action."""
        if action in (DeviceAction.VOLUME_UP, DeviceAction.VOLUME_DOWN):
            return self.settings.volume_interval_sec, self._last_volume_action_at
        return self.settings.brightness_interval_sec, self._last_brightness_action_at

    def _note_continuous_action(self, action: DeviceAction, now: float) -> None:
        if action in (DeviceAction.VOLUME_UP, DeviceAction.VOLUME_DOWN):
            self._last_volume_action_at = now
        else:
            self._last_brightness_action_at = now

    def _rate_limited(
        self,
        action: DeviceAction,
        now: float,
        capability: Optional[DeviceCapability],
        interval: float,
    ) -> DeviceActionResult:
        """Report a refused action truthfully instead of silently dropping it."""
        result = DeviceActionResult(
            False,
            action,
            "ACTION COOLDOWN",
            now,
            capability,
            f"RATE LIMITED ({interval:.2f}s)",
        )
        self._record(result, silent=True)
        return result

    def run_action(self, action: DeviceAction, step: float = DIRECT_STEP) -> DeviceActionResult:
        """Run one allowlisted action once, through the same gates as a gesture.

        This is the single direct entry point for a request that did not come
        from a gesture - VisionCore AI uses it, and any future interface button
        can too. The runner is the same call the gesture handlers make, so the
        behaviour, the rate policy and the honesty of the result are identical:
        the action is refused when control is not enabled, when the platform does
        not offer the capability, and when the backend declines.
        """
        magnitude = max(1.0, min(25.0, float(step)))
        runners = {
            DeviceAction.VOLUME_UP: lambda: self._apply_volume(magnitude),
            DeviceAction.VOLUME_DOWN: lambda: self._apply_volume(-magnitude),
            DeviceAction.MUTE: self.backend.mute_toggle,
            DeviceAction.PLAY_PAUSE: lambda: self.backend.media(MediaAction.PLAY_PAUSE),
            DeviceAction.NEXT_TRACK: lambda: self.backend.media(MediaAction.NEXT),
            DeviceAction.PREVIOUS_TRACK: lambda: self.backend.media(MediaAction.PREVIOUS),
            DeviceAction.BRIGHTNESS_UP: lambda: self._apply_brightness(magnitude),
            DeviceAction.BRIGHTNESS_DOWN: lambda: self._apply_brightness(-magnitude),
            DeviceAction.MINIMIZE: lambda: self.backend.window(WindowAction.MINIMIZE),
            DeviceAction.MAXIMIZE: lambda: self.backend.window(WindowAction.MAXIMIZE),
            DeviceAction.NEXT_WINDOW: lambda: self.backend.window(WindowAction.NEXT),
        }
        runner = runners.get(action)
        if runner is None:
            return DeviceActionResult(
                False, action, "ACTION NOT AVAILABLE", self._now(), action.capability
            )
        result = self.perform(action, runner=runner)
        if result.success and action is DeviceAction.VOLUME_UP:
            self._volume_steps += 1
        elif result.success and action is DeviceAction.VOLUME_DOWN:
            self._volume_steps -= 1
        return result

    # -- per frame --------------------------------------------------------- #

    def update(
        self,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
        now: Optional[float] = None,
    ) -> DeviceSnapshot:
        """Advance the device layer by one frame and return its snapshot.

        ``now`` defaults to the monotonic clock and can be supplied explicitly so
        the time dependent policy can be validated deterministically.
        """
        now = time.perf_counter() if now is None else now
        self._frame_now = now
        hand = self._control_hand(tracking, gesture)

        if self._state is ControlState.DISABLED:
            self._cancel_continuous()
            return self._publish(now)

        if self._state in (ControlState.PAUSED, ControlState.EMERGENCY_STOP):
            self._cancel_continuous()
            return self._publish(now)

        if self._mode is not ControlMode.DEVICE:
            # Mouse mode: the device layer is armed but deliberately inert, so a
            # pinch can only ever be a click.
            self._cancel_continuous()
            self._open_palm_since = None
            self._mute_cycle_active = False
            return self._publish(now)

        present = tracking.detected and hand is not None and tracking.state.is_engaged
        verdict = self._gate.evaluate(now, present, _confidence(hand, tracking))
        if not verdict.healthy:
            self._cancel_continuous()
            self._open_palm_since = None
            self._suspended = True
            self._suspended_reason = (
                f"DEVICE ACTIONS SUSPENDED / {verdict.reason}" if verdict.reason
                else "DEVICE ACTIONS SUSPENDED"
            )
            self._state = ControlState.ARMED
            return self._publish(now)

        self._suspended = False
        self._suspended_reason = ""
        self._state = ControlState.ACTIVE
        self._refresh_levels()

        result = gesture.result
        recognised = result.recognized
        gesture_kind = result.gesture if recognised else Gesture.NONE

        self._handle_open_palm(now, gesture_kind, result.phase)
        if self._state is ControlState.EMERGENCY_STOP:
            return self._publish(now)

        self._handle_volume(now, hand, gesture_kind)
        self._handle_brightness(now, hand, gesture_kind)
        self._handle_mute(now, gesture_kind)
        self._handle_media(now, gesture_kind)

        return self._publish(now)

    # -- gesture handlers -------------------------------------------------- #

    def _handle_open_palm(
        self, now: float, gesture: Gesture, phase: GesturePhase
    ) -> None:
        """Short open palm: play/pause. Clearly longer hold: emergency stop."""
        if gesture is not Gesture.OPEN_PALM:
            self._open_palm_since = None
            return

        if self._open_palm_since is None:
            self._open_palm_since = now
            # Fire once per gesture cycle, while the gesture is still starting.
            self._event_action(
                now, DeviceAction.PLAY_PAUSE, lambda: self.backend.media(MediaAction.PLAY_PAUSE)
            )
            return

        if now - self._open_palm_since >= self.settings.emergency_stop_sec:
            self.emergency_stop("OPEN PALM HELD")

    def _handle_volume(self, now: float, hand: Optional[Hand], gesture: Gesture) -> None:
        """Two finger vertical travel controls volume, travel driven and limited."""
        if gesture is not Gesture.TWO_FINGER or hand is None:
            self._volume_travel = 0.0
            self._reset_travel("volume")
            return
        if not self.backend.supports(DeviceCapability.VOLUME):
            self._note_unavailable(DeviceCapability.VOLUME)
            return

        delta = self._travel(hand, "volume", self.settings.volume_deadzone)
        if delta is None:
            return
        # Screen up is a decreasing image y and means "louder". Travel is
        # accumulated, so a slow deliberate movement still acts while tremor
        # never crosses the deadzone.
        self._volume_travel += -delta
        if abs(self._volume_travel) < self.settings.volume_deadzone:
            return
        if not self._rate_ok(now, self._last_volume_action_at, self.settings.volume_interval_sec):
            return

        requested = self._volume_travel * self.settings.volume_sensitivity
        step = max(-self.settings.volume_max_step, min(self.settings.volume_max_step, requested))
        step = int(round(step))
        if step == 0:
            return
        self._volume_travel -= step / self.settings.volume_sensitivity
        self._last_volume_action_at = now

        action = DeviceAction.VOLUME_UP if step > 0 else DeviceAction.VOLUME_DOWN
        result = self._execute(
            action, now, lambda: self._apply_volume(step), DeviceCapability.VOLUME
        )
        if result.success:
            self._volume_steps += 1 if step > 0 else -1
        self._record(result, silent=not result.success)

    def _handle_brightness(
        self, now: float, hand: Optional[Hand], gesture: Gesture
    ) -> None:
        """Fist vertical travel controls display brightness when supported.

        ``POINT + TWO_FINGER`` cannot be used here: the gesture engine resolves a
        single primary gesture per hand and those two poses are geometrically
        exclusive, so the combination would be unreliable. A fist is unambiguous
        and is unused by every other control mapping.
        """
        if gesture is not Gesture.FIST or hand is None:
            self._brightness_travel = 0.0
            self._reset_travel("brightness")
            return
        capability = DeviceCapability.BRIGHTNESS
        if not self.backend.supports(capability):
            self._note_unavailable(capability)
            return

        delta = self._travel(hand, "brightness", self.settings.brightness_deadzone)
        if delta is None:
            return
        self._brightness_travel += -delta
        if abs(self._brightness_travel) < self.settings.brightness_deadzone:
            return
        if not self._rate_ok(
            now, self._last_brightness_action_at, self.settings.brightness_interval_sec
        ):
            return

        requested = self._brightness_travel * self.settings.brightness_sensitivity
        step = max(
            -self.settings.brightness_max_step,
            min(self.settings.brightness_max_step, requested),
        )
        step = int(round(step))
        if step == 0:
            return
        self._brightness_travel -= step / self.settings.brightness_sensitivity
        self._last_brightness_action_at = now

        action = (
            DeviceAction.BRIGHTNESS_UP if step > 0 else DeviceAction.BRIGHTNESS_DOWN
        )
        result = self._execute(
            action, now, lambda: self._apply_brightness(step), capability
        )
        self._record(result, silent=not result.success)

    def _handle_mute(self, now: float, gesture: Gesture) -> None:
        """Pinch toggles mute, exactly once per pinch cycle."""
        if not self.settings.pinch_mutes:
            return
        if gesture is not Gesture.PINCH:
            self._mute_cycle_active = False
            return
        if self._mute_cycle_active:
            return
        self._mute_cycle_active = True
        self._event_action(now, DeviceAction.MUTE, self.backend.mute_toggle)

    def _handle_media(self, now: float, gesture: Gesture) -> None:
        """Swipes skip tracks; the gesture engine already fires one event per swipe."""
        if gesture is Gesture.SWIPE_RIGHT:
            self._event_action(
                now, DeviceAction.NEXT_TRACK, lambda: self.backend.media(MediaAction.NEXT)
            )
        elif gesture is Gesture.SWIPE_LEFT:
            self._event_action(
                now,
                DeviceAction.PREVIOUS_TRACK,
                lambda: self.backend.media(MediaAction.PREVIOUS),
            )

    # -- plumbing ---------------------------------------------------------- #

    def _event_action(self, now: float, action: DeviceAction, runner) -> None:
        """Run an event action once per gesture cycle, subject to the cooldown."""
        capability = action.capability
        if not self.backend.supports(capability):
            self._note_unavailable(capability)
            return
        last = self._last_action_at.get(action)
        if last is not None and now - last < self.settings.action_cooldown_sec:
            return
        result = self._execute(action, now, runner, capability)
        if result.success:
            self._last_action_at[action] = now
        self._record(result, silent=not result.success)

    def _execute(
        self,
        action: DeviceAction,
        now: float,
        runner,
        capability: Optional[DeviceCapability],
        detail: str = "",
    ) -> DeviceActionResult:
        """Apply the remaining gates and run the backend call exactly once."""
        if self._state not in (ControlState.ARMED, ControlState.ACTIVE):
            return DeviceActionResult(
                False, action, "CONTROL NOT ENABLED", now, capability, "DEVICE DISABLED"
            )
        if self._mode is not ControlMode.DEVICE:
            # Mode gate at the controller itself, not only at the caller: while
            # the application is in MOUSE mode the device layer can never act,
            # whoever asks - a gesture handler, VisionCore AI or an interface
            # button. A mode change is the only way to arm it.
            return DeviceActionResult(
                False, action, "DEVICE MODE REQUIRED", now, capability, "MOUSE MODE ACTIVE"
            )
        if capability is not None and not self.backend.supports(capability):
            return DeviceActionResult(
                False, action, f"{capability.label} UNAVAILABLE", now, capability,
                self._capability_detail(capability),
            )
        if runner is None:
            return DeviceActionResult(False, action, "NO ACTION", now, capability)

        try:
            ok = bool(runner())
        except Exception as exc:  # a backend must never take the application down
            logger.warning("Device action %s failed: %s", action.value, exc)
            ok = False

        message = action.label if ok else f"{action.label} FAILED"
        return DeviceActionResult(
            ok, action, message, now, capability, detail if ok else "BACKEND REFUSED"
        )

    def _apply_volume(self, step: float) -> bool:
        """Absolute set when the platform reports a level, relative keys when not."""
        if self._volume is None:
            return self.backend.volume_step(1 if step > 0 else -1)
        target = max(0.0, min(1.0, self._volume + step / 100.0))
        if not self.backend.volume_set(target):
            return False
        self._volume = target
        return True

    def _apply_brightness(self, step: float) -> bool:
        if self._brightness is None:
            self._brightness = self.backend.brightness_level()
        if self._brightness is None:
            return False
        target = max(0.0, min(1.0, self._brightness + step / 100.0))
        if not self.backend.brightness_set(target):
            return False
        self._brightness = target
        return True

    def _refresh_levels(self, force: bool = False) -> None:
        """Re-read the platform levels occasionally, never every frame."""
        now = self._now()
        if not force and now - self._level_read_at < self.settings.level_refresh_sec:
            return
        self._level_read_at = now
        if self.backend.supports(DeviceCapability.VOLUME):
            level = self.backend.volume_level()
            if level is not None:
                self._volume = level
            state = self.backend.mute_state()
            if state is not None:
                self._muted = state
        if self.backend.supports(DeviceCapability.BRIGHTNESS):
            level = self.backend.brightness_level()
            if level is not None:
                self._brightness = level

    def _travel(self, hand: Hand, key: str, deadzone: float) -> Optional[float]:
        """Vertical palm travel since the previous frame, for one control.

        The anchor is the mean height of the wrist and the middle knuckle, which
        is stable through finger movement, so the measured travel is the hand's
        own movement rather than a pose change.
        """
        if len(hand.landmarks) <= _TRAVEL_ANCHORS[1]:
            return None
        anchor = (
            hand.landmarks[_TRAVEL_ANCHORS[0]].y + hand.landmarks[_TRAVEL_ANCHORS[1]].y
        ) / 2.0
        previous = self._travel_anchors.get(key)
        self._travel_anchors[key] = anchor
        if previous is None:
            return None
        delta = anchor - previous
        if abs(delta) < deadzone * 0.25:
            # Sub-pixel jitter is not travel: ignore it so the accumulator only
            # ever sees deliberate movement.
            return None
        return delta

    def _reset_travel(self, key: str) -> None:
        self._travel_anchors.pop(key, None)

    def _rate_ok(self, now: float, last: float, interval: float) -> bool:
        return now - last >= interval

    def _cancel_continuous(self) -> None:
        self._volume_travel = 0.0
        self._brightness_travel = 0.0
        self._travel_anchors.clear()

    def _note_unavailable(self, capability: DeviceCapability) -> None:
        """Report a missing platform capability, at most once every few seconds."""
        now = self._now()
        last = self._notes.get(capability, 0.0)
        message = f"{capability.label} UNAVAILABLE"
        if now - last < _NOTE_INTERVAL_SEC:
            return
        self._notes[capability] = now
        self._message = message
        self._last_result = DeviceActionResult(
            False, None, message, now, capability, self._capability_detail(capability)
        )
        logger.debug("Device capability missing: %s", capability.label)

    def _capability_detail(self, capability: DeviceCapability) -> str:
        for report in self.backend.capabilities():
            if report.capability is capability:
                return report.detail
        return ""

    def _record(self, result: DeviceActionResult, silent: bool = False) -> None:
        self._last_result = result
        if silent:
            return
        # Every user visible result counts as an event - including a refusal, so
        # the interface can report it honestly instead of showing a success.
        self._action_events += 1
        if result.success:
            self._actions += 1
            if self._message.endswith("UNAVAILABLE"):
                self._message = ""
        logger.debug("Device action %s -> %s", result.action, result.message)

    def _control_hand(
        self, tracking: TrackingSnapshot, gesture: GestureSnapshot
    ) -> Optional[Hand]:
        if not tracking.detected:
            return None
        handedness = gesture.result.handedness
        if handedness:
            for hand in tracking.hands:
                if hand.handedness == handedness:
                    return hand
        return tracking.primary

    def _now(self) -> float:
        return self._frame_now if self._frame_now is not None else time.perf_counter()

    def _publish(self, now: float) -> DeviceSnapshot:
        self._snapshot = self._build_snapshot(now)
        return self._snapshot

    def _build_snapshot(self, now: float, action_age: Optional[float] = None) -> DeviceSnapshot:
        result = self._last_result
        reports = self.backend.capabilities()
        capabilities = tuple(
            (report.capability.label, report.available) for report in reports
        )
        capability_details = tuple(
            (report.capability.label, report.detail) for report in reports
        )
        if action_age is None:
            action_age = now - result.timestamp if result is not None else 0.0

        return DeviceSnapshot(
            state_label=self._state.label,
            mode_label=self._mode.label,
            enabled=self._state is not ControlState.DISABLED,
            active=self._state is ControlState.ACTIVE,
            backend=self.backend.name,
            message=self._message,
            suspended=self._suspended,
            suspended_reason=self._suspended_reason,
            capability_summary=capabilities,
            capability_details=capability_details,
            volume=self._volume,
            volume_known=self._volume is not None,
            volume_steps=self._volume_steps,
            muted=self._muted,
            brightness=self._brightness,
            brightness_known=self._brightness is not None,
            action_label=result.message if result is not None else "",
            action_success=result.success if result is not None else True,
            action_detail=result.detail if result is not None else "",
            action_age=action_age,
            actions_performed=self._actions,
            action_events=self._action_events,
            emergency_stops=self._emergency_stops,
            launchable=self.backend.launchable(),
        )



def _confidence(hand: Optional[Hand], tracking: TrackingSnapshot) -> float:
    """Measured tracking confidence (the same value mouse control consumes)."""
    scores = []
    if hand is not None and hand.confidence is not None:
        scores.append(float(hand.confidence))
    if tracking.result.confidence is not None:
        scores.append(float(tracking.result.confidence))
    return min(scores) if scores else 0.0
