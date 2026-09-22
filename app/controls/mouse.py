"""Touchless mouse controller for VisionCore.

This is the only module in the project that performs an operating system input
action. It consumes the tracking and gesture results produced by the layers
before it and never performs recognition itself:

    Gesture engine  ->  MouseController  ->  PlatformMouseBackend  ->  OS cursor

Design rules enforced here:

* Control starts ``DISABLED`` on every launch and only an explicit user action
  arms it.
* The pointer only moves while the ``POINT`` gesture is held, so ordinary hand
  movement cannot drag the cursor around.
* A pinch produces exactly one click; holding the pinch turns it into a drag.
  One pinch cycle can never emit more than one click.
* Any loss of trust (hand lost, low confidence, tracking dropped, pause,
  emergency stop, shutdown) immediately releases a held button.
"""

from __future__ import annotations

import atexit
import logging
import time
from typing import Optional

from app.controls.backend import MouseButton, PlatformMouseBackend, create_backend
from app.controls.mouse_mapper import CursorMapper
from app.controls.safety import (
    ControlAction,
    ControlCounters,
    ControlSettings,
    ControlSnapshot,
    ControlState,
    SafetyGate,
)
from app.gestures import Gesture, GesturePhase, GestureSnapshot
from app.hand_tracking import FINGER_JOINTS, Hand, TrackingSnapshot

logger = logging.getLogger("visioncore.controls.mouse")

# Landmark of the index fingertip: the point that drives the cursor.
INDEX_TIP = FINGER_JOINTS["INDEX"][-1]
# Stable palm anchors used for scroll tracking (wrist and middle knuckle).
SCROLL_ANCHORS = (0, FINGER_JOINTS["MIDDLE"][0])

# Backend write failures tolerated in a row before control is disarmed, so a
# broken backend cannot leave the user with a system that silently does nothing.
MAX_CONSECUTIVE_FAILURES = 45

# Fallback frame interval for the very first smoothing step.
DEFAULT_FRAME_DT = 1.0 / 30.0


class MouseController:
    """Stateful, safety-first controller that maps gestures onto the mouse."""

    def __init__(
        self,
        settings: Optional[ControlSettings] = None,
        backend: Optional[PlatformMouseBackend] = None,
    ) -> None:
        self.settings = (settings or ControlSettings()).clamped()
        self.backend = backend if backend is not None else create_backend()
        self._gate = SafetyGate(self.settings)
        self._mapper: Optional[CursorMapper] = None

        self._state = ControlState.DISABLED
        self._message = ""
        self._pointer_active = False
        self._dragging = False
        self._scrolling = False
        self._pinch_seen = False
        self._pinch_started_at = 0.0
        self._pinch_clicked = False
        self._last_click_at: Optional[float] = None
        self._scroll_accumulator = 0.0
        self._scroll_travel = 0.0
        self._scroll_anchor: Optional[float] = None
        self._open_palm_since: Optional[float] = None
        self._action = ControlAction.NONE
        self._action_at = 0.0
        # Clock for the frame being processed; ``None`` means "use the monotonic
        # clock". Keeps every time dependent decision on one timebase.
        self._frame_now: Optional[float] = None
        self._held_button: Optional[MouseButton] = None
        self._failures = 0
        self._counters = ControlCounters()
        self._snapshot = ControlSnapshot(
            state=ControlState.DISABLED,
            available=self.available,
            backend=self.backend.name,
            message=self._startup_message(),
        )
        atexit.register(self._final_release)

    # -- introspection ----------------------------------------------------- #

    @property
    def state(self) -> ControlState:
        return self._state

    @property
    def available(self) -> bool:
        return bool(self.settings.enabled and self.backend.available)

    @property
    def message(self) -> str:
        return self._message

    @property
    def snapshot(self) -> ControlSnapshot:
        return self._snapshot

    @property
    def pointer_active(self) -> bool:
        return self._pointer_active

    def _startup_message(self) -> str:
        if not self.settings.enabled:
            return "DISABLED IN CONFIGURATION"
        if not self.backend.available:
            return self.backend.reason
        return ""

    # -- user commands ----------------------------------------------------- #

    def enable(self) -> bool:
        """Arm the controller. Returns False (with a reason) when impossible."""
        if not self.settings.enabled:
            self._message = "DISABLED IN CONFIGURATION"
            logger.info("Mouse control cannot be enabled: %s", self._message)
            return False
        if not self.backend.available:
            self._message = self.backend.reason
            logger.warning("Mouse control unavailable: %s", self.backend.detail)
            return False

        geometry = self.backend.screen_geometry()
        if geometry is None:
            self._message = "DESKTOP GEOMETRY UNAVAILABLE"
            logger.warning("Mouse control cannot be enabled: no desktop geometry")
            return False

        if self._mapper is None:
            self._mapper = CursorMapper(self.settings, geometry)
        else:
            self._mapper.set_settings(self.settings)
            self._mapper.set_geometry(geometry)

        self._gate.reset()
        self._state = ControlState.ARMED
        self._message = "" if self.backend.supports_buttons else "CLICKS UNAVAILABLE"
        logger.info(
            "Mouse control armed (backend=%s, desktop=%dx%d)",
            self.backend.name,
            geometry.width,
            geometry.height,
        )
        return True

    def disable(self) -> None:
        """Fully disarm control and release everything the controller holds."""
        self._release_pointer_state()
        self._state = ControlState.DISABLED
        self._message = self._startup_message()
        logger.info("Mouse control disabled")

    def pause(self) -> None:
        """Suspend actions without losing tracking (user requested)."""
        if self._state in (ControlState.ARMED, ControlState.ACTIVE):
            self._release_pointer_state()
            self._state = ControlState.PAUSED
            self._message = ""
            logger.info("Mouse control paused")

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
        logger.info("Mouse control resumed")

    def toggle(self) -> bool:
        """Primary UI action: enable, pause or resume depending on state."""
        if self._state is ControlState.DISABLED:
            return self.enable()
        if self._state in (ControlState.ARMED, ControlState.ACTIVE):
            self.pause()
        else:
            self.resume()
        return True

    def _now(self) -> float:
        """Current frame time (monotonic by default)."""
        return self._frame_now if self._frame_now is not None else time.perf_counter()

    def emergency_stop(self, reason: str = "EMERGENCY STOP") -> None:
        """Trip the emergency stop: everything is released immediately."""
        if self._state in (ControlState.DISABLED, ControlState.EMERGENCY_STOP):
            return
        self._release_pointer_state()
        self._state = ControlState.EMERGENCY_STOP
        self._counters.emergency_stops += 1
        self._set_action(ControlAction.EMERGENCY_STOP)
        self._message = reason
        logger.warning("Emergency stop: %s", reason)

    def on_tracking_lost(self) -> None:
        """Called when the tracking pipeline itself stops (camera loss)."""
        if self._state is not ControlState.DISABLED:
            self._release_pointer_state()
            self._state = ControlState.ARMED if self.available else ControlState.DISABLED
        self._gate.reset()

    def close(self) -> None:
        """Fail-safe teardown: release any button, then close the backend."""
        self._release_pointer_state()
        self._state = ControlState.DISABLED
        try:
            atexit.unregister(self._final_release)
        except Exception:
            pass
        try:
            self.backend.close()
        except Exception as exc:
            logger.warning("Error closing mouse backend: %s", exc)

    # -- per frame --------------------------------------------------------- #

    def update(
        self,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
        now: Optional[float] = None,
    ) -> ControlSnapshot:
        """Advance the control layer by one frame and return its snapshot.

        ``now`` defaults to the monotonic clock; it can be supplied explicitly so
        time dependent policy (click cooldown, drag dwell, emergency stop window)
        can be validated deterministically, exactly as the gesture engine allows.
        """
        now = time.perf_counter() if now is None else now
        self._frame_now = now
        hand = self._control_hand(tracking, gesture)
        frame_dt = dt if 0.0 < dt < 0.5 else DEFAULT_FRAME_DT

        if self._state is ControlState.DISABLED:
            self._release_pointer_state()
            return self._publish(self._snapshot_for(now, hand))

        if self._state is ControlState.PAUSED:
            return self._publish(
                self._snapshot_for(now, hand, message="TRACKING CONTINUES")
            )

        if self._state is ControlState.EMERGENCY_STOP:
            self._release_pointer_state()
            return self._publish(
                self._snapshot_for(now, hand, message=self._message or "EMERGENCY STOP")
            )

        # --- safety gate: is the evidence trustworthy right now? ---------- #
        present = tracking.detected and hand is not None and tracking.state.is_engaged
        verdict = self._gate.evaluate(now, present, self._confidence(hand, tracking))

        if not verdict.healthy:
            self._release_pointer_state()
            return self._publish(
                self._snapshot_for(
                    now, hand, message=verdict.reason, suspended=True, reason=verdict.reason
                )
            )

        result = gesture.result
        recognised = result.recognized

        # --- emergency stop: a stable open palm --------------------------- #
        if recognised and result.gesture is Gesture.OPEN_PALM:
            self._open_palm_since = self._open_palm_since or now
            if now - self._open_palm_since >= self.settings.emergency_stop_sec:
                self.emergency_stop("OPEN PALM")
                return self._publish(
                    self._snapshot_for(now, hand, message="EMERGENCY STOP")
                )
        else:
            self._open_palm_since = None

        pointing = recognised and result.gesture is Gesture.POINT
        pinching = recognised and result.gesture is Gesture.PINCH
        two_finger = recognised and result.gesture is Gesture.TWO_FINGER

        self._update_pointer(frame_dt, hand, result.gesture, result.phase, pointing, pinching)
        self._update_clicks(now, pinching)
        self._update_scroll(hand, two_finger)

        if self._failures >= MAX_CONSECUTIVE_FAILURES:
            logger.error("Mouse backend stopped accepting input; disarming control")
            self.disable()
            self._message = "BACKEND UNAVAILABLE"

        return self._publish(self._snapshot_for(now, hand))

    # -- pointer ----------------------------------------------------------- #

    def _update_pointer(
        self,
        dt: float,
        hand: Optional[Hand],
        gesture: Gesture,
        phase: GesturePhase,
        pointing: bool,
        pinching: bool,
    ) -> None:
        """Latch the pointer to POINT and move the cursor while it is held.

        The pointer can only be engaged by an explicit ``POINT``: a pinch or a
        two finger pose on its own never takes control of the cursor, and it can
        therefore never click. Once engaged, the pointer is held through a pinch
        (so a click or a drag is possible) and released as soon as the hand stops
        pointing.
        """
        # Gestures that may keep an already engaged pointer during their
        # transition frames.
        continuing = pinching or (
            gesture in (Gesture.POINT, Gesture.PINCH) and phase is GesturePhase.RELEASE
        )

        if pointing and not self._pointer_active:
            self._pointer_active = True
            self._state = ControlState.ACTIVE
            self._set_action(ControlAction.POINTER_ENGAGED)
            if self._mapper is not None:
                self._mapper.reset()
        elif self._pointer_active and not (pointing or continuing):
            self._pointer_active = False
            if self._state is ControlState.ACTIVE:
                self._state = ControlState.ARMED
            self._set_action(ControlAction.POINTER_RELEASED)
            if self._mapper is not None:
                self._mapper.reset()

        if not self._pointer_active or hand is None or self._mapper is None:
            return
        if len(hand.landmarks) <= INDEX_TIP:
            return

        tip = hand.landmarks[INDEX_TIP]
        pixel = self._mapper.update(dt, (tip.x, tip.y))
        if pixel is None:
            return
        if self.backend.move_absolute(pixel[0], pixel[1]):
            self._failures = 0
            self._mapper.mark_commanded(pixel)
        else:
            self._failures += 1

    # -- clicks and drag --------------------------------------------------- #

    def _update_clicks(self, now: float, pinching: bool) -> None:
        """One pinch cycle yields one click; holding it escalates to a drag."""
        if pinching:
            if not self._pinch_seen:
                self._pinch_seen = True
                self._pinch_started_at = now
                self._pinch_clicked = False
                if (
                    self._pointer_active
                    and self.backend.supports_buttons
                    and (
                        self._last_click_at is None
                        or now - self._last_click_at >= self.settings.click_cooldown_sec
                    )
                ):
                    self._click(now)
            elif (
                self._pinch_clicked
                and not self._dragging
                and self._pointer_active
                and self.backend.supports_buttons
                and now - self._pinch_started_at >= self.settings.drag_hold_sec
            ):
                self._begin_drag()
            return

        # Pinch gone (cancelled or released): always end any drag.
        if self._dragging:
            self._end_drag()
        self._pinch_seen = False
        self._pinch_clicked = False

    def _click(self, now: float) -> None:
        pressed = self.backend.button_down(MouseButton.LEFT)
        released = self.backend.button_up(MouseButton.LEFT)
        if not released:
            # Never leave a button logically held: retry the release once.
            released = self.backend.button_up(MouseButton.LEFT)
        if not (pressed and released):
            self._failures += 1
            return
        self._failures = 0
        self._last_click_at = now
        self._pinch_clicked = True
        self._counters.clicks += 1
        self._set_action(ControlAction.LEFT_CLICK)
        logger.debug("Left click issued (total=%d)", self._counters.clicks)

    def _begin_drag(self) -> None:
        if not self.backend.button_down(MouseButton.LEFT):
            self._failures += 1
            return
        self._failures = 0
        self._dragging = True
        self._held_button = MouseButton.LEFT
        self._set_action(ControlAction.DRAG_START)
        logger.debug("Drag started")

    def _end_drag(self) -> None:
        held = self._held_button or MouseButton.LEFT
        if self.backend.button_up(held):
            self._failures = 0
        else:
            self._failures += 1
            self.backend.button_up(held)  # second attempt before giving up
        self._dragging = False
        self._held_button = None
        self._set_action(ControlAction.DRAG_END)
        logger.debug("Drag ended")

    # -- scrolling --------------------------------------------------------- #

    def _update_scroll(self, hand: Optional[Hand], two_finger: bool) -> None:
        """Movement driven scrolling: no movement means no scrolling."""
        if not two_finger or hand is None or len(hand.landmarks) <= SCROLL_ANCHORS[1]:
            self._scrolling = False
            self._scroll_anchor = None
            self._scroll_accumulator = 0.0
            self._scroll_travel = 0.0
            return

        anchor = _palm_anchor_y(hand)
        if not self._scrolling:
            self._scrolling = True
            self._scroll_anchor = anchor
            self._scroll_accumulator = 0.0
            self._scroll_travel = 0.0
            return

        previous = self._scroll_anchor
        self._scroll_anchor = anchor
        if previous is None:
            return

        dy = anchor - previous
        # The deadzone is a total travel gate, not a per frame filter: a slow but
        # deliberate movement eventually scrolls, while tremor and a hand that is
        # simply held up accumulate nothing to act on.
        self._scroll_travel += abs(dy)
        if self._scroll_travel < self.settings.scroll_deadzone:
            return

        # Screen up is a decreasing image y, and positive wheel notches scroll up.
        self._scroll_accumulator += -dy * self.settings.scroll_sensitivity
        if abs(dy) < self.settings.scroll_deadzone * 0.25:
            # Hand nearly still: bleed off the partial notch so drift cannot build
            # into an unwanted scroll while the pose is merely held.
            self._scroll_accumulator *= 0.6
        if abs(self._scroll_accumulator) < 1.0:
            return

        notches = int(self._scroll_accumulator)
        limit = self.settings.scroll_max_notches
        notches = max(-limit, min(limit, notches))
        self._scroll_accumulator -= notches
        if notches == 0 or not self.backend.supports_buttons:
            return

        if self.backend.scroll(notches):
            self._failures = 0
            self._counters.scroll_events += 1
            self._set_action(
                ControlAction.SCROLL_UP if notches > 0 else ControlAction.SCROLL_DOWN
            )
        else:
            self._failures += 1

    # -- helpers ----------------------------------------------------------- #

    def _control_hand(
        self, tracking: TrackingSnapshot, gesture: GestureSnapshot
    ) -> Optional[Hand]:
        """The hand control acts on: the one the gesture result came from."""
        if not tracking.detected:
            return None
        handedness = gesture.result.handedness
        if handedness:
            for hand in tracking.hands:
                if hand.handedness == handedness:
                    return hand
        return tracking.primary

    @staticmethod
    def _confidence(hand: Optional[Hand], tracking: TrackingSnapshot) -> float:
        """Measured tracking confidence for the hand under control."""
        scores = []
        if hand is not None and hand.confidence is not None:
            scores.append(float(hand.confidence))
        if tracking.result.confidence is not None:
            scores.append(float(tracking.result.confidence))
        return min(scores) if scores else 0.0

    def _release_pointer_state(self) -> None:
        """Stop every interaction and make sure no button stays pressed."""
        if self._dragging:
            self._end_drag()
        self._pointer_active = False
        self._scrolling = False
        self._scroll_anchor = None
        self._scroll_accumulator = 0.0
        self._scroll_travel = 0.0
        self._pinch_seen = False
        self._pinch_clicked = False
        self._open_palm_since = None
        if self._state is ControlState.ACTIVE:
            self._state = ControlState.ARMED
        if self._mapper is not None:
            self._mapper.reset()

    def _set_action(self, action: ControlAction) -> None:
        self._action = action
        self._action_at = self._now()

    def _snapshot_for(
        self,
        now: float,
        hand: Optional[Hand],
        message: Optional[str] = None,
        suspended: bool = False,
        reason: str = "",
    ) -> ControlSnapshot:
        """Build the immutable view of the control layer for this frame."""
        cursor = None
        screen = None
        pixel = None
        desktop = None

        if self._pointer_active and hand is not None and len(hand.landmarks) > INDEX_TIP:
            tip = hand.landmarks[INDEX_TIP]
            cursor = (tip.x, tip.y)
            if self._mapper is not None:
                screen = self._mapper.position
                pixel = self._mapper.current_pixel()
                desktop = (self._mapper.geometry.width, self._mapper.geometry.height)

        return ControlSnapshot(
            state=self._state,
            available=self.available,
            backend=self.backend.name,
            message=self._message if message is None else message,
            pointer_active=self._pointer_active,
            dragging=self._dragging,
            scrolling=self._scrolling,
            suspended=suspended,
            suspended_reason=reason,
            action=self._action,
            action_age=now - self._action_at if self._action_at else 0.0,
            cursor=cursor,
            screen=screen,
            pixel=pixel,
            desktop=desktop,
            clicks=self._counters.clicks,
            scroll_events=self._counters.scroll_events,
            emergency_stops=self._counters.emergency_stops,
        )

    def _publish(self, snapshot: ControlSnapshot) -> ControlSnapshot:
        self._snapshot = snapshot
        return snapshot

    def _final_release(self) -> None:
        """Last-resort release executed at interpreter exit.

        Guarantees the process can never exit while a mouse button is logically
        held, even when the normal shutdown path was skipped.
        """
        if self._dragging:
            try:
                self.backend.button_up(self._held_button or MouseButton.LEFT)
            except Exception:
                pass
        self._dragging = False
        self._held_button = None


def _palm_anchor_y(hand: Hand) -> float:
    """Vertical palm anchor, in normalised frame coordinates."""
    landmarks = hand.landmarks
    return (landmarks[SCROLL_ANCHORS[0]].y + landmarks[SCROLL_ANCHORS[1]].y) / 2.0
