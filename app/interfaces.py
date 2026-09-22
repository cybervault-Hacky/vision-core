"""Architectural interfaces and protocols for future VisionCore extension phases.

Phase 1 provides these abstract contracts as architectural blueprints for:
- Phase 2: Hand Landmark Tracking
- Phase 3: Dynamic Gesture Recognition
- Phase 4: Touchless Device & Cursor Control

NOTE: These interfaces are structural definitions only and are intentionally
unimplemented in Phase 1 per project specifications.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


class GestureType(str, Enum):
    """Enumeration of planned future gesture classifications."""

    NONE = "NONE"
    OPEN_PALM = "OPEN_PALM"
    FIST = "FIST"
    POINT = "POINT"
    PINCH = "PINCH"
    SWIPE_LEFT = "SWIPE_LEFT"
    SWIPE_RIGHT = "SWIPE_RIGHT"
    SWIPE_UP = "SWIPE_UP"
    SWIPE_DOWN = "SWIPE_DOWN"


@dataclass(frozen=True)
class NormalizedLandmark:
    """Represents a single 3D spatial coordinate normalized to [0.0, 1.0]."""

    x: float
    y: float
    z: float = 0.0
    visibility: float = 1.0


@dataclass
class TrackingResult:
    """Encapsulates spatial hand tracking telemetry for a single video frame."""

    landmarks: List[NormalizedLandmark] = field(default_factory=list)
    handedness: str = "Unknown"  # "Left", "Right", or "Unknown"
    confidence: float = 0.0
    bounding_box: Optional[Tuple[int, int, int, int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GestureEvent:
    """Dispatched when a discrete or continuous gesture is recognized."""

    gesture: GestureType = GestureType.NONE
    confidence: float = 0.0
    origin: Tuple[float, float] = (0.0, 0.0)
    timestamp: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)


class ITrackingPipeline(ABC):
    """Interface for future Phase 2 hand landmark detection backends."""

    @abstractmethod
    def initialize(self) -> bool:
        """Prepare tracker neural network models and GPU/CPU pipelines."""
        raise NotImplementedError("Tracking pipeline will be implemented in Phase 2.")

    @abstractmethod
    def process(self, frame: np.ndarray) -> List[TrackingResult]:
        """Detect and localize hand landmarks from a raw RGB frame."""
        raise NotImplementedError("Tracking pipeline will be implemented in Phase 2.")

    @abstractmethod
    def release(self) -> None:
        """Free neural network graphs and tracking acceleration contexts."""
        raise NotImplementedError("Tracking pipeline will be implemented in Phase 2.")


class IGestureClassifier(ABC):
    """Interface for future Phase 3 gesture classification backends."""

    @abstractmethod
    def classify(self, tracking_results: List[TrackingResult]) -> Optional[GestureEvent]:
        """Map spatial hand landmark configurations to gesture classifications."""
        raise NotImplementedError("Gesture classification will be implemented in Phase 3.")


class IDeviceController(ABC):
    """Interface for future Phase 4 touchless device execution backends."""

    @abstractmethod
    def dispatch(self, event: GestureEvent) -> bool:
        """Translate recognized gesture events into OS cursor/keyboard/media actions."""
        raise NotImplementedError("Device control will be implemented in Phase 4.")
