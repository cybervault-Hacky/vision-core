"""Configuration management system for VisionCore."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("visioncore.config")

DEFAULT_CONFIG_FILENAME = "config.json"


@dataclass
class AppConfig:
    """Application configuration for VisionCore Phase 1."""

    # Camera settings
    camera_index: int = 0
    mirror_camera: bool = True
    target_fps: int = 30
    camera_width: int = 1280
    camera_height: int = 720

    # Display & UI settings
    window_title: str = "VISIONCORE // ADVANCED VISION SYSTEM"
    window_width: int = 1024
    window_height: int = 700
    min_window_width: int = 800
    min_window_height: int = 600
    fullscreen: bool = False

    # Futuristic UI & Boot settings
    boot_duration_sec: float = 2.4
    scan_animation_speed: float = 1.0
    show_debug: bool = False
    subtle_glow: bool = True

    # Developer & diagnostic options
    mock_camera: bool = False
    log_level: str = "INFO"

    # Future extension slots (Phase 2+)
    future_preferences: Dict[str, Any] = field(default_factory=dict)

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

        if self.scan_animation_speed <= 0.0:
            self.scan_animation_speed = 1.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppConfig:
        """Create AppConfig from dictionary with type-safe conversion."""
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        cfg = cls(**filtered)
        cfg.validate()
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return asdict(self)

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

    def save(self, path: Optional[Path | str] = None) -> bool:
        """Serialize configuration to a JSON file."""
        config_path = Path(path) if path else Path(DEFAULT_CONFIG_FILENAME)
        try:
            self.validate()
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=4)
            logger.info("Configuration saved to %s", config_path)
            return True
        except Exception as exc:
            logger.error("Failed to save configuration to %s: %s", config_path, exc)
            return False
