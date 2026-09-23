"""Typed data model for the VisionCore gesture recognition engine.

The engine is deliberately independent of the camera, the UI and any operating
system control: it consumes hand landmarks and emits :class:`GestureResult`
values that the HUD and the safety-gated control layers can read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

# Normalised image position (x, y) used for landmark derived geometry.
Point = Tuple[float, float]

EMPTY_EVIDENCE: Mapping[str, object] = MappingProxyType({})


class Gesture(str, Enum):
    """Gestures recognised by the Phase 3 engine."""

    NONE = "NONE"
    OPEN_PALM = "OPEN_PALM"
    FIST = "FIST"
    POINT = "POINT"
    PINCH = "PINCH"
    TWO_FINGER = "TWO_FINGER"
    SWIPE_LEFT = "SWIPE_LEFT"
    SWIPE_RIGHT = "SWIPE_RIGHT"

    @property
    def is_motion(self) -> bool:
        """True for gestures detected from hand movement rather than pose."""
        return self in (Gesture.SWIPE_LEFT, Gesture.SWIPE_RIGHT)

    @property
    def label(self) -> str:
        """Human readable form used by the HUD."""
        return self.value.replace("_", " ")


class GesturePhase(str, Enum):
    """Lifecycle of a recognised gesture, mirroring press/release semantics."""

    NONE = "NONE"        # nothing recognised
    START = "START"      # first frame of a newly stable gesture
    ACTIVE = "ACTIVE"    # gesture held
    RELEASE = "RELEASE"  # gesture ended on this frame


class GestureState(str, Enum):
    """Engine level state reported to the HUD."""

    DISABLED = "DISABLED"
    SEARCHING = "SEARCHING"      # no tracked hand, nothing to analyse
    ANALYZING = "ANALYZING"      # hand tracked, awaiting a stable pose
    RECOGNIZED = "RECOGNIZED"    # a stable gesture is held

    @property
    def display_label(self) -> str:
        """Short status string for panels and overlays."""
        if self is GestureState.RECOGNIZED:
            return "ACTIVE"
        return self.value


@dataclass(frozen=True, slots=True)
class GestureResult:
    """Outcome of one recognition pass for a single hand.

    ``changed`` marks a transition (START or RELEASE) while ``active`` reports
    whether a gesture is currently held - the distinction the control layers use
    to turn a pinch into a press and a release into a lift.
    """

    gesture: Gesture = Gesture.NONE
    phase: GesturePhase = GesturePhase.NONE
    confidence: float = 0.0
    active: bool = False
    changed: bool = False
    released: Gesture = Gesture.NONE
    handedness: Optional[str] = None
    timestamp: float = 0.0
    evidence: Mapping[str, object] = field(default_factory=lambda: EMPTY_EVIDENCE)

    @property
    def recognized(self) -> bool:
        """True while a non-neutral gesture is held."""
        return self.active and self.gesture is not Gesture.NONE


@dataclass(frozen=True, slots=True)
class GestureSnapshot:
    """Immutable view of the gesture engine for one frame."""

    state: GestureState
    result: GestureResult
    hands: Tuple[GestureResult, ...] = ()
    latency_ms: float = 0.0

    @property
    def primary(self) -> GestureResult:
        """Gestures recognised for the primary (most confident) hand."""
        return self.result

    @property
    def enabled(self) -> bool:
        return self.state is not GestureState.DISABLED


@dataclass(frozen=True, slots=True)
class GestureSettings:
    """Tunable recognition parameters with performance-safe defaults.

    All distance thresholds are expressed in hand-relative or frame-relative
    normalised units, never in pixels, so recognition is unaffected by camera
    resolution, hand size or distance from the camera.
    """

    enabled: bool = True

    # Pose stabilisation
    confidence_threshold: float = 0.62
    stability_frames: int = 3
    release_frames: int = 2

    # Pinch (thumb tip to index tip, normalised by palm scale)
    pinch_threshold: float = 0.72
    pinch_release_threshold: float = 0.85
    pinch_lift_threshold: float = 1.25

    # Swipe (horizontal displacement of the palm centre over a short window)
    swipe_distance_threshold: float = 0.18
    swipe_velocity_threshold: float = 0.60
    swipe_cooldown: float = 0.70
    swipe_window_sec: float = 0.40
    swipe_min_duration_sec: float = 0.08
    swipe_min_samples: int = 4
    swipe_consistency_threshold: float = 0.70
    swipe_axis_ratio: float = 0.60
    swipe_hold_sec: float = 0.45

    # Hand session bookkeeping
    session_timeout_sec: float = 1.0
    hand_jump_threshold: float = 0.35
    max_sessions: int = 4

    def clamped(self) -> "GestureSettings":
        """Return a copy with every value forced into a sane operating range."""
        def _bounded(value: float, low: float, high: float) -> float:
            return max(low, min(high, value))

        return GestureSettings(
            enabled=bool(self.enabled),
            confidence_threshold=_bounded(self.confidence_threshold, 0.30, 0.95),
            stability_frames=int(_bounded(self.stability_frames, 1, 15)),
            release_frames=int(_bounded(self.release_frames, 1, 15)),
            pinch_threshold=_bounded(self.pinch_threshold, 0.20, 1.20),
            pinch_release_threshold=_bounded(
                max(self.pinch_release_threshold, self.pinch_threshold + 0.02), 0.25, 1.40
            ),
            pinch_lift_threshold=_bounded(self.pinch_lift_threshold, 0.60, 2.50),
            swipe_distance_threshold=_bounded(self.swipe_distance_threshold, 0.05, 0.90),
            swipe_velocity_threshold=_bounded(self.swipe_velocity_threshold, 0.10, 6.0),
            swipe_cooldown=_bounded(self.swipe_cooldown, 0.05, 3.0),
            swipe_window_sec=_bounded(self.swipe_window_sec, 0.10, 1.50),
            swipe_min_duration_sec=_bounded(self.swipe_min_duration_sec, 0.02, 0.60),
            swipe_min_samples=int(_bounded(self.swipe_min_samples, 2, 16)),
            swipe_consistency_threshold=_bounded(self.swipe_consistency_threshold, 0.30, 0.98),
            swipe_axis_ratio=_bounded(self.swipe_axis_ratio, 0.30, 0.95),
            swipe_hold_sec=_bounded(self.swipe_hold_sec, 0.10, 1.50),
            session_timeout_sec=_bounded(self.session_timeout_sec, 0.20, 10.0),
            hand_jump_threshold=_bounded(self.hand_jump_threshold, 0.05, 1.0),
            max_sessions=int(_bounded(self.max_sessions, 1, 8)),
        )
