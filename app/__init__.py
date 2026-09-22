"""VisionCore application core package."""

from app.camera import CameraManager
from app.config import AppConfig
from app.interfaces import (
    GestureEvent,
    GestureType,
    IDeviceController,
    IGestureClassifier,
    ITrackingPipeline,
    NormalizedLandmark,
    TrackingResult,
)
from app.logger import setup_logger
from app.state import AppState, SubsystemState, Telemetry

__all__ = [
    "AppConfig",
    "AppState",
    "CameraManager",
    "GestureEvent",
    "GestureType",
    "IDeviceController",
    "IGestureClassifier",
    "ITrackingPipeline",
    "NormalizedLandmark",
    "SubsystemState",
    "Telemetry",
    "TrackingResult",
    "setup_logger",
]
