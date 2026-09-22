"""State machine and telemetry structures for VisionCore."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from app.controls.safety import (
    ControlAction,
    ControlSnapshot,
    ControlState,
)
from app.gestures.types import (
    Gesture,
    GesturePhase,
    GestureSnapshot,
    GestureState,
)
from app.hand_tracking import TrackingState


class AppState(str, Enum):
    """High-level application lifecycle states."""

    BOOTING = "BOOTING"
    CAMERA_ACTIVE = "CAMERA_ACTIVE"
    CAMERA_ERROR = "CAMERA_ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"


class SubsystemState(str, Enum):
    """Granular state of an individual vision or control subsystem."""

    ONLINE = "ONLINE"
    CHECKING = "CHECKING"
    INITIALIZING = "INITIALIZING"
    SEARCHING = "SEARCHING"
    ACTIVE = "ACTIVE"
    LOST = "LOST"
    STANDBY = "STANDBY"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


# Truthful subsystem reporting for the control layer: enabled states are shown
# in amber, an engaged pointer in mint, a tripped safety stop in red.
_CONTROL_SUBSYSTEM = {
    ControlState.DISABLED: SubsystemState.DISABLED,
    ControlState.ARMED: SubsystemState.STANDBY,
    ControlState.ACTIVE: SubsystemState.ACTIVE,
    ControlState.PAUSED: SubsystemState.STANDBY,
    ControlState.EMERGENCY_STOP: SubsystemState.ERROR,
}


@dataclass
class Telemetry:
    """Live telemetry and status data consumed by HUD and diagnostics."""

    app_state: AppState = AppState.BOOTING

    # Subsystem statuses (always reflect real capability)
    vision_core: SubsystemState = SubsystemState.ONLINE
    camera: SubsystemState = SubsystemState.CHECKING
    tracking: SubsystemState = SubsystemState.STANDBY
    gestures: SubsystemState = SubsystemState.STANDBY
    control: SubsystemState = SubsystemState.DISABLED

    # Camera feed metrics
    camera_width: int = 0
    camera_height: int = 0
    camera_fps: float = 0.0
    camera_backend: str = "UNKNOWN"
    camera_index: int = 0
    mirrored: bool = True

    # Mouse control metrics (real state and real counters only)
    control_state: ControlState = ControlState.DISABLED
    control_message: str = ""
    control_backend: str = "UNSUPPORTED"
    control_available: bool = False
    control_action: ControlAction = ControlAction.NONE
    control_action_age: float = 0.0
    control_suspended: bool = False
    pointer_active: bool = False
    pointer_dragging: bool = False
    pointer_scrolling: bool = False
    pointer_x: Optional[float] = None
    pointer_y: Optional[float] = None
    control_clicks: int = 0
    control_scroll_events: int = 0
    control_emergency_stops: int = 0

    # Gesture recognition metrics (geometry derived, never simulated)
    gesture: Gesture = Gesture.NONE
    gesture_state: GestureState = GestureState.SEARCHING
    gesture_phase: GesturePhase = GesturePhase.NONE
    gesture_confidence: Optional[float] = None
    gesture_latency_ms: float = 0.0
    gesture_events: int = 0

    # Hand tracking metrics (all values are measured, never simulated)
    tracking_state: TrackingState = TrackingState.NO_HAND
    hands_detected: int = 0
    hand_handedness: Optional[str] = None
    hand_confidence: Optional[float] = None
    tracker_fps: float = 0.0
    tracking_latency_ms: float = 0.0
    tracking_engine: str = "MEDIAPIPE HANDS"
    tracking_dropped_frames: int = 0
    max_hands: int = 1

    # Performance telemetry
    render_fps: float = 0.0
    frame_count: int = 0
    dropped_frames: int = 0
    start_time: float = field(default_factory=time.time)

    # Error handling context
    error_title: Optional[str] = None
    error_message: Optional[str] = None
    error_instructions: List[str] = field(default_factory=list)

    @property
    def uptime_seconds(self) -> float:
        """Return elapsed application uptime in seconds."""
        return max(0.0, time.time() - self.start_time)

    @property
    def formatted_uptime(self) -> str:
        """Format uptime as HH:MM:SS."""
        secs = int(self.uptime_seconds)
        hours = secs // 3600
        mins = (secs % 3600) // 60
        seconds = secs % 60
        return f"{hours:02d}:{mins:02d}:{seconds:02d}"

    def set_tracking_state(self, state: TrackingState) -> None:
        """Mirror the tracker lifecycle into the subsystem matrix."""
        self.tracking_state = state
        if state in (TrackingState.DETECTING, TrackingState.TRACKING):
            self.tracking = SubsystemState.ACTIVE
        elif state is TrackingState.HAND_LOST:
            self.tracking = SubsystemState.LOST
        else:
            self.tracking = SubsystemState.SEARCHING

    def update_gestures(self, snapshot: GestureSnapshot) -> None:
        """Mirror a gesture recognition snapshot into the telemetry model."""
        self.gesture_state = snapshot.state
        self.gesture_latency_ms = snapshot.latency_ms
        result = snapshot.result
        self.gesture_phase = result.phase

        if result.recognized:
            self.gesture = result.gesture
            self.gesture_confidence = result.confidence
        elif result.phase is GesturePhase.RELEASE:
            # Transition frame: keep reporting the gesture that just ended.
            self.gesture = result.gesture
            self.gesture_confidence = result.confidence or None
        else:
            self.gesture = Gesture.NONE
            self.gesture_confidence = None

        if result.changed:
            self.gesture_events += 1

        if snapshot.state is GestureState.DISABLED:
            self.gestures = SubsystemState.DISABLED
        elif snapshot.state is GestureState.RECOGNIZED:
            self.gestures = SubsystemState.ACTIVE
        elif snapshot.state is GestureState.SEARCHING:
            self.gestures = SubsystemState.SEARCHING
        else:
            self.gestures = SubsystemState.ONLINE

    def update_control(self, snapshot: ControlSnapshot) -> None:
        """Mirror a mouse control snapshot into the telemetry model."""
        self.control_state = snapshot.state
        self.control_message = snapshot.message
        self.control_backend = snapshot.backend
        self.control_available = snapshot.available
        self.control_action = snapshot.action
        self.control_action_age = snapshot.action_age
        self.control_suspended = snapshot.suspended
        self.pointer_active = snapshot.pointer_active
        self.pointer_dragging = snapshot.dragging
        self.pointer_scrolling = snapshot.scrolling
        self.control_clicks = snapshot.clicks
        self.control_scroll_events = snapshot.scroll_events
        self.control_emergency_stops = snapshot.emergency_stops

        screen = snapshot.screen
        if snapshot.pointer_active and screen is not None:
            self.pointer_x = screen[0]
            self.pointer_y = screen[1]
        else:
            self.pointer_x = None
            self.pointer_y = None

        self.control = _CONTROL_SUBSYSTEM[snapshot.state]

    def set_control_unavailable(self) -> None:
        """Mark device control as not running (shutdown / camera loss)."""
        self.control_state = ControlState.DISABLED
        self.control_action = ControlAction.NONE
        self.pointer_active = False
        self.pointer_dragging = False
        self.pointer_scrolling = False
        self.pointer_x = None
        self.pointer_y = None
        self.control = SubsystemState.DISABLED

    def set_gestures_unavailable(self) -> None:
        """Mark gesture recognition as not running (disabled or unavailable)."""
        self.gesture = Gesture.NONE
        self.gesture_confidence = None
        self.gesture_phase = GesturePhase.NONE
        self.gesture_state = GestureState.DISABLED
        self.gesture_latency_ms = 0.0
        self.gestures = SubsystemState.UNAVAILABLE

    def set_camera_error(
        self,
        title: str,
        message: str,
        instructions: Optional[List[str]] = None,
    ) -> None:
        """Configure error telemetry for camera failure."""
        self.app_state = AppState.CAMERA_ERROR
        self.camera = SubsystemState.UNAVAILABLE
        self.tracking = SubsystemState.UNAVAILABLE
        self.tracking_state = TrackingState.NO_HAND
        self.hands_detected = 0
        self.hand_handedness = None
        self.hand_confidence = None
        self.gesture = Gesture.NONE
        self.gesture_confidence = None
        self.gesture_phase = GesturePhase.NONE
        self.gesture_state = GestureState.DISABLED
        self.gestures = SubsystemState.UNAVAILABLE
        self.error_title = title
        self.error_message = message
        self.error_instructions = instructions or [
            "Verify your physical camera is connected securely.",
            "Verify operating system camera permissions are granted.",
            "Confirm no other application (Zoom, Teams, browser) is locking the camera.",
        ]

    def set_camera_online(self, width: int, height: int, fps: float, backend: str) -> None:
        """Configure telemetry when camera becomes actively streamable."""
        self.app_state = AppState.CAMERA_ACTIVE
        self.camera = SubsystemState.ONLINE
        self.camera_width = width
        self.camera_height = height
        self.camera_fps = fps
        self.camera_backend = backend
        self.error_title = None
        self.error_message = None
        self.error_instructions.clear()
