"""State machine and telemetry structures for VisionCore."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

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
