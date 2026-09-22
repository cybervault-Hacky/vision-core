"""Configuration management system for VisionCore."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # imported lazily in the settings builders to keep imports shallow
    from app.controls.safety import ControlSettings
    from app.gestures.types import GestureSettings

logger = logging.getLogger("visioncore.config")

DEFAULT_CONFIG_FILENAME = "config.json"


@dataclass
class AppConfig:
    """Application configuration for VisionCore."""

    # Camera settings
    camera_index: int = 0
    mirror_camera: bool = True
    target_fps: int = 30
    camera_width: int = 1280
    camera_height: int = 720

    # Display & UI settings
    window_title: str = "VISIONCORE // ADVANCED VISION SYSTEM"
    window_width: int = 1100
    window_height: int = 820
    min_window_width: int = 800
    min_window_height: int = 600
    fullscreen: bool = False

    # Hand tracking settings
    tracking_enabled: bool = True
    max_hands: int = 1
    tracking_model_complexity: int = 0      # 0 = lite (fast), 1 = full (accurate)
    tracking_input_width: int = 640         # inference downscale target, 0 = native
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5

    # Gesture recognition settings (geometry based, no additional model)
    gesture_enabled: bool = True
    gesture_confidence_threshold: float = 0.62   # minimum geometric evidence
    gesture_stability_frames: int = 3            # consecutive agreeing frames
    gesture_release_frames: int = 2              # consecutive frames to release
    pinch_threshold: float = 0.72                # tip separation / palm scale
    pinch_release_threshold: float = 0.85        # hysteresis while pinching
    pinch_lift_threshold: float = 1.25           # pinch point distance from palm
    swipe_distance_threshold: float = 0.18       # normalised travel
    swipe_velocity_threshold: float = 0.60       # normalised units per second
    swipe_cooldown: float = 0.70                 # seconds between swipes
    swipe_window_sec: float = 0.40               # motion history window

    # Touchless mouse control (opt-in: control always starts disabled)
    mouse_control_enabled: bool = True
    cursor_smoothing: float = 0.55               # 0 = raw, 1 = heavy damping
    cursor_speed: float = 1.0                    # pointer gain
    cursor_deadzone: float = 0.006               # normalised jitter rejection
    control_region_margin: float = 0.15          # frame border excluded from control
    click_cooldown: float = 0.45                 # seconds between two clicks
    drag_hold_sec: float = 0.30                  # pinch hold that starts a drag
    scroll_sensitivity: float = 8.0              # wheel notches per normalised unit
    scroll_deadzone: float = 0.015               # ignores tremor while scrolling
    safety_confidence_threshold: float = 0.45    # below this, actions are suspended
    emergency_stop_sec: float = 0.80             # stable open palm before the stop

    # Boot & UI settings
    boot_duration_sec: float = 2.4
    show_debug: bool = False

    # Diagnostic options
    mock_camera: bool = False
    log_level: str = "INFO"

    def validate(self) -> None:
        """Validate and clamp configuration parameters to safe operating ranges."""
        if self.camera_index < 0:
            logger.warning(
                "Invalid camera_index=%d; resetting to default 0", self.camera_index
            )
            self.camera_index = 0

        if not (10 <= self.target_fps <= 120):
            clamped = max(10, min(120, self.target_fps))
            logger.warning(
                "Target FPS %d out of bounds [10, 120]; clamping to %d",
                self.target_fps,
                clamped,
            )
            self.target_fps = clamped

        if self.min_window_width < 640:
            self.min_window_width = 640
        if self.min_window_height < 480:
            self.min_window_height = 480

        if self.window_width < self.min_window_width:
            self.window_width = self.min_window_width
        if self.window_height < self.min_window_height:
            self.window_height = self.min_window_height

        if self.boot_duration_sec < 0.5:
            self.boot_duration_sec = 0.5
        elif self.boot_duration_sec > 10.0:
            self.boot_duration_sec = 10.0

        if not (1 <= self.max_hands <= 4):
            clamped = max(1, min(4, self.max_hands))
            logger.warning(
                "max_hands %d out of bounds [1, 4]; clamping to %d", self.max_hands, clamped
            )
            self.max_hands = clamped

        if self.tracking_model_complexity not in (0, 1):
            logger.warning(
                "tracking_model_complexity must be 0 or 1; resetting to 0"
            )
            self.tracking_model_complexity = 0

        if self.tracking_input_width < 0:
            self.tracking_input_width = 0
        elif 0 < self.tracking_input_width < 240:
            self.tracking_input_width = 240

        for name, value in (
            ("min_detection_confidence", self.min_detection_confidence),
            ("min_tracking_confidence", self.min_tracking_confidence),
        ):
            if not (0.05 <= value <= 0.95):
                clamped = max(0.05, min(0.95, value))
                logger.warning("%s %.2f out of bounds [0.05, 0.95]; clamping to %.2f", name, value, clamped)
                setattr(self, name, clamped)

        if self.gesture_confidence_threshold < 0.30 or self.gesture_confidence_threshold > 0.95:
            self.gesture_confidence_threshold = max(
                0.30, min(0.95, self.gesture_confidence_threshold)
            )
            logger.warning(
                "gesture_confidence_threshold out of bounds; clamping to %.2f",
                self.gesture_confidence_threshold,
            )

        if self.pinch_threshold <= 0.0 or self.pinch_threshold > 1.40:
            self.pinch_threshold = 0.72
            logger.warning("pinch_threshold out of bounds; resetting to %.2f", self.pinch_threshold)

        if self.pinch_release_threshold <= self.pinch_threshold:
            self.pinch_release_threshold = self.pinch_threshold + 0.13

        for name, low, high in (
            ("cursor_smoothing", 0.0, 0.95),
            ("cursor_speed", 0.20, 3.0),
            ("cursor_deadzone", 0.0, 0.10),
            ("control_region_margin", 0.0, 0.40),
            ("click_cooldown", 0.05, 3.0),
            ("drag_hold_sec", 0.15, 2.0),
            ("scroll_sensitivity", 0.5, 25.0),
            ("scroll_deadzone", 0.0, 0.20),
            ("safety_confidence_threshold", 0.05, 0.95),
            ("emergency_stop_sec", 0.20, 5.0),
            ("swipe_distance_threshold", 0.05, 0.90),
            ("swipe_velocity_threshold", 0.10, 6.0),
            ("swipe_cooldown", 0.05, 3.0),
            ("swipe_window_sec", 0.10, 1.50),
        ):
            value = getattr(self, name)
            if not (low <= value <= high):
                clamped = max(low, min(high, value))
                logger.warning("%s %.2f out of bounds [%.2f, %.2f]; clamping to %.2f", name, value, low, high, clamped)
                setattr(self, name, clamped)

    def control_settings(self) -> "ControlSettings":
        """Build the mouse control settings from the application configuration."""
        from app.controls.safety import ControlSettings

        return ControlSettings(
            enabled=self.mouse_control_enabled and self.gesture_enabled and self.tracking_enabled,
            cursor_smoothing=self.cursor_smoothing,
            cursor_speed=self.cursor_speed,
            cursor_deadzone=self.cursor_deadzone,
            control_region_margin=self.control_region_margin,
            click_cooldown_sec=self.click_cooldown,
            drag_hold_sec=self.drag_hold_sec,
            scroll_sensitivity=self.scroll_sensitivity,
            scroll_deadzone=self.scroll_deadzone,
            safety_confidence_threshold=self.safety_confidence_threshold,
            emergency_stop_sec=self.emergency_stop_sec,
        ).clamped()

    def gesture_settings(self) -> "GestureSettings":
        """Build the gesture engine settings from the application configuration."""
        from app.gestures.types import GestureSettings

        return GestureSettings(
            enabled=self.gesture_enabled and self.tracking_enabled,
            confidence_threshold=self.gesture_confidence_threshold,
            stability_frames=self.gesture_stability_frames,
            release_frames=self.gesture_release_frames,
            pinch_threshold=self.pinch_threshold,
            pinch_release_threshold=self.pinch_release_threshold,
            pinch_lift_threshold=self.pinch_lift_threshold,
            swipe_distance_threshold=self.swipe_distance_threshold,
            swipe_velocity_threshold=self.swipe_velocity_threshold,
            swipe_cooldown=self.swipe_cooldown,
            swipe_window_sec=self.swipe_window_sec,
            max_sessions=max(1, self.max_hands),
        ).clamped()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppConfig:
        """Create AppConfig from dictionary with type-safe conversion."""
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        cfg = cls(**filtered)
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> AppConfig:
        """Load configuration from JSON file or return defaults if absent/corrupted."""
        config_path = Path(path) if path else Path(DEFAULT_CONFIG_FILENAME)

        if not config_path.exists():
            logger.debug(
                "Configuration file '%s' not found; using defaults", config_path
            )
            return cls()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = json.load(f)
            if not isinstance(content, dict):
                logger.warning(
                    "Configuration root must be an object; using default settings"
                )
                return cls()
            cfg = cls.from_dict(content)
            logger.info("Loaded configuration from %s", config_path)
            return cfg
        except json.JSONDecodeError as exc:
            logger.warning(
                "Malformed JSON in '%s' (%s); falling back to default settings",
                config_path,
                exc,
            )
            return cls()
        except Exception as exc:
            logger.warning(
                "Error reading '%s' (%s); falling back to default settings",
                config_path,
                exc,
            )
            return cls()
