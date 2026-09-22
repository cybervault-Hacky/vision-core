"""Local gesture recognition engine for VisionCore."""

from app.gestures.classifier import POSE_PRIORITY, PoseCandidate, classify, select
from app.gestures.engine import GestureEngine
from app.gestures.features import HandFeatures, extract
from app.gestures.temporal import MotionHistory, SwipeDetector, SwipeEvent
from app.gestures.types import (
    Gesture,
    GesturePhase,
    GestureResult,
    GestureSettings,
    GestureSnapshot,
    GestureState,
)

__all__ = [
    "Gesture",
    "GestureEngine",
    "GesturePhase",
    "GestureResult",
    "GestureSettings",
    "GestureSnapshot",
    "GestureState",
    "HandFeatures",
    "MotionHistory",
    "POSE_PRIORITY",
    "PoseCandidate",
    "SwipeDetector",
    "SwipeEvent",
    "classify",
    "extract",
    "select",
]
