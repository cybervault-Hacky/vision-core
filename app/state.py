"""State machine and telemetry structures for VisionCore."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from app.controls.device_types import DeviceAction, DeviceSnapshot
from app.controls.safety import (
    ControlAction,
    ControlMode,
    ControlSnapshot,
    ControlState,
)
from app.gestures.types import (
    Gesture,
    GesturePhase,
    GestureSnapshot,
    GestureState,
)
from app.ai.types import AISnapshot
from app.hand_tracking import TrackingState
from app.interaction.feedback import ActionFeedback
from app.interaction.states import InteractionSnapshot


# Label maps keep the telemetry model free of presentation logic while still
# storing typed values.
_STATE_FROM_LABEL = {
    "DISABLED": ControlState.DISABLED,
    "ARMED": ControlState.ARMED,
    "ACTIVE": ControlState.ACTIVE,
    "PAUSED": ControlState.PAUSED,
    "EMERGENCY STOP": ControlState.EMERGENCY_STOP,
}
_MODE_FROM_LABEL = {mode.label: mode for mode in ControlMode}


def _action_from_label(label: str) -> Optional[DeviceAction]:
    """Map a human readable action label back to its typed action."""
    if not label:
        return None
    for action in DeviceAction:
        if label == action.label or label == f"{action.label} FAILED":
            return action
    return None


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
    device: SubsystemState = SubsystemState.DISABLED

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
    control_action_events: int = 0

    # Device control metrics (typed actions and real capability reports)
    control_mode: ControlMode = ControlMode.MOUSE
    device_state: ControlState = ControlState.DISABLED
    device_message: str = ""
    device_backend: str = "UNSUPPORTED"
    device_available: bool = False
    device_action: Optional[DeviceAction] = None
    device_action_label: str = ""
    device_action_success: bool = True
    device_action_age: float = 0.0
    device_volume: Optional[float] = None
    device_volume_known: bool = False
    device_volume_steps: int = 0
    device_muted: Optional[bool] = None
    device_brightness: Optional[float] = None
    device_brightness_known: bool = False
    device_suspended: bool = False
    device_suspended_reason: str = ""
    device_capabilities: Dict[str, bool] = field(default_factory=dict)
    device_capability_notes: Dict[str, str] = field(default_factory=dict)
    device_launchable: Dict[str, str] = field(default_factory=dict)
    device_action_detail: str = ""
    device_actions: int = 0
    device_action_events: int = 0
    device_emergency_stops: int = 0

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
    tracking_error: str = ""
    tracking_lock_progress: float = 0.0
    tracking_dropped_frames: int = 0
    max_hands: int = 1

    # Performance telemetry (every value is measured; 0 means "not available")
    render_fps: float = 0.0
    control_latency_ms: float = 0.0
    frame_count: int = 0
    dropped_frames: int = 0
    start_time: float = field(default_factory=time.time)
    diagnostics_visible: bool = True

    # Interaction experience (Phase 6). The director derives these from the real
    # subsystem state above; the subsystem state remains authoritative.
    interaction: InteractionSnapshot = field(default_factory=InteractionSnapshot)
    feedback: Tuple[ActionFeedback, ...] = ()
    toast: Optional[ActionFeedback] = None

    # AI assistant (Phase 7). A snapshot only: the assistant owns its own state,
    # the conversation lives in memory and nothing here is ever persisted.
    ai: AISnapshot = field(default_factory=AISnapshot)
    ai_panel_visible: bool = False

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
        self.control_action_events = snapshot.action_events

        screen = snapshot.screen
        if snapshot.pointer_active and screen is not None:
            self.pointer_x = screen[0]
            self.pointer_y = screen[1]
        else:
            self.pointer_x = None
            self.pointer_y = None

        self.control = _CONTROL_SUBSYSTEM[snapshot.state]

    def update_device(self, snapshot: DeviceSnapshot) -> None:
        """Mirror a device control snapshot into the telemetry model."""
        self.device_state = _STATE_FROM_LABEL.get(snapshot.state_label, ControlState.DISABLED)
        self.control_mode = _MODE_FROM_LABEL.get(snapshot.mode_label, ControlMode.MOUSE)
        self.device_message = snapshot.message
        self.device_backend = snapshot.backend
        self.device_available = any(available for _, available in snapshot.capability_summary)
        self.device_action = _action_from_label(snapshot.action_label)
        self.device_action_label = snapshot.action_label
        self.device_action_success = snapshot.action_success
        self.device_action_age = snapshot.action_age
        self.device_volume = snapshot.volume
        self.device_volume_known = snapshot.volume_known
        self.device_volume_steps = snapshot.volume_steps
        self.device_muted = snapshot.muted
        self.device_brightness = snapshot.brightness
        self.device_brightness_known = snapshot.brightness_known
        self.device_suspended = snapshot.suspended
        self.device_suspended_reason = snapshot.suspended_reason
        self.device_capabilities = snapshot.capability_map
        self.device_capability_notes = snapshot.capability_notes
        self.device_launchable = dict(snapshot.launchable)
        self.device_action_detail = snapshot.action_detail
        self.device_actions = snapshot.actions_performed
        self.device_action_events = snapshot.action_events
        self.device_emergency_stops = snapshot.emergency_stops
        self.device = _CONTROL_SUBSYSTEM[self.device_state]

    def update_interaction(
        self,
        snapshot: InteractionSnapshot,
        feedback: Tuple[ActionFeedback, ...] = (),
        toast: Optional[ActionFeedback] = None,
    ) -> None:
        """Publish the interaction layer for this frame.

        ``feedback`` is the in-memory recent-action timeline (newest first) and
        ``toast`` is the notification currently shown, if any. Both are produced
        by the interaction director from real action results.
        """
        self.interaction = snapshot
        self.feedback = feedback
        self.toast = toast

    def update_ai(self, snapshot: AISnapshot) -> None:
        """Publish the assistant snapshot for this frame."""
        self.ai = snapshot

    def set_device_unavailable(self) -> None:
        """Mark device control as not running (shutdown / camera loss)."""
        self.device_state = ControlState.DISABLED
        self.device_action = None
        self.device_action_label = ""
        self.device_suspended = False
        self.device = SubsystemState.DISABLED

    def set_control_mode(self, mode: ControlMode) -> None:
        self.control_mode = mode

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
