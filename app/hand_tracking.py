"""Local hand tracking engine for VisionCore.

MediaPipe's hand landmark solution is wrapped in a dedicated inference worker so
the render loop never blocks on model execution. Everything happens on-device:
frames are read from memory, processed locally, and discarded. No frame, image or
landmark ever leaves the machine and no network transport is used.

The emitted data model is intentionally gesture-agnostic: a future gesture engine
can consume :class:`HandTrackingResult` without changes to this module.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterator, Optional, Tuple

# Keep the native MediaPipe / TFLite runtime quiet before it is imported.
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import cv2
import numpy as np

logger = logging.getLogger("visioncore.tracking")

# --------------------------------------------------------------------------- #
# Hand topology (MediaPipe hand landmark model)
# --------------------------------------------------------------------------- #

# Ordered names of the 21 landmarks produced by the hand landmark model. They
# give every finger (and the wrist) an addressable identity for the visual layer
# and for the gesture engine planned for a later phase.
LANDMARK_NAMES: Tuple[str, ...] = (
    "WRIST",
    "THUMB_CMC", "THUMB_MCP", "THUMB_IP", "THUMB_TIP",
    "INDEX_MCP", "INDEX_PIP", "INDEX_DIP", "INDEX_TIP",
    "MIDDLE_MCP", "MIDDLE_PIP", "MIDDLE_DIP", "MIDDLE_TIP",
    "RING_MCP", "RING_PIP", "RING_DIP", "RING_TIP",
    "PINKY_MCP", "PINKY_PIP", "PINKY_DIP", "PINKY_TIP",
)

WRIST = LANDMARK_NAMES.index("WRIST")

# Joint chain of every finger, ordered base to tip, as landmark indices:
# (MCP, PIP, DIP, TIP) for the four fingers and (CMC, MCP, IP, TIP) for the
# thumb. Shared by the visual layer and the gesture engine so landmark topology
# has a single source of truth.
FINGER_JOINTS: Dict[str, Tuple[int, int, int, int]] = {
    "THUMB": (1, 2, 3, 4),
    "INDEX": (5, 6, 7, 8),
    "MIDDLE": (9, 10, 11, 12),
    "RING": (13, 14, 15, 16),
    "PINKY": (17, 18, 19, 20),
}

# Bone connections used for visualisation. Palm edges are drawn a little
# stronger than finger phalanges by the HUD renderer.
HAND_CONNECTIONS: Tuple[Tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),        # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # index
    (5, 9), (9, 10), (10, 11), (11, 12),   # middle
    (9, 13), (13, 14), (14, 15), (15, 16),  # ring
    (13, 17), (17, 18), (18, 19), (19, 20),  # pinky
    (0, 17),                               # palm base
)

PALM_CONNECTIONS = frozenset({(0, 5), (5, 9), (9, 13), (13, 17), (0, 17)})


# --------------------------------------------------------------------------- #
# Tracking states
# --------------------------------------------------------------------------- #


class TrackingState(str, Enum):
    """Lifecycle of the tracking pipeline as observed by the HUD."""

    NO_HAND = "NO_HAND"        # nothing in view, pipeline searching
    DETECTING = "DETECTING"    # hand observed, lock-on still stabilising
    TRACKING = "TRACKING"      # locked on a stable hand
    HAND_LOST = "HAND_LOST"    # tracked hand disappeared, grace window running

    @property
    def is_engaged(self) -> bool:
        """True while a hand is currently being followed."""
        return self in (TrackingState.DETECTING, TrackingState.TRACKING)

    @property
    def status_label(self) -> str:
        """Short, human readable status string for panels and overlays."""
        if self is TrackingState.TRACKING:
            return "LOCKED"
        if self is TrackingState.DETECTING:
            return "ACQUIRING"
        if self is TrackingState.HAND_LOST:
            return "LOST"
        return "SEARCHING"


# --------------------------------------------------------------------------- #
# Tracked hand data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Landmark:
    """A single hand landmark in normalised frame space (mirroring included)."""

    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class BoundingRegion:
    """Axis-aligned bounding region of a hand, normalised to the frame."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.width * 0.5, self.y + self.height * 0.5)


@dataclass(frozen=True, slots=True)
class Hand:
    """One tracked hand."""

    landmarks: Tuple[Landmark, ...]
    handedness: Optional[str]
    confidence: Optional[float]
    bounding_box: BoundingRegion
    tracked: bool


@dataclass(frozen=True, slots=True)
class HandTrackingResult:
    """Structured output of a single inference pass.

    Structured exactly for downstream consumption (Phase 3 gesture engine):
    ``detected``, ``landmarks``, ``confidence``, ``handedness``,
    ``bounding_box`` and ``timestamp``.
    """

    detected: bool
    hands: Tuple[Hand, ...]
    landmarks: Tuple[Landmark, ...]
    confidence: Optional[float]
    handedness: Optional[str]
    bounding_box: Optional[BoundingRegion]
    timestamp: float
    inference_ms: float

    @classmethod
    def empty(cls, timestamp: float = 0.0, inference_ms: float = 0.0) -> "HandTrackingResult":
        """Build a valid 'no hand present' result."""
        return cls(
            detected=False,
            hands=(),
            landmarks=(),
            confidence=None,
            handedness=None,
            bounding_box=None,
            timestamp=timestamp,
            inference_ms=inference_ms,
        )


@dataclass(frozen=True, slots=True)
class TrackingSnapshot:
    """Immutable view of the tracking pipeline, safe to pass to the UI thread."""

    state: TrackingState
    result: HandTrackingResult
    inference_ms: float
    tracker_fps: float
    dropped_frames: int

    @property
    def detected(self) -> bool:
        return self.result.detected

    @property
    def hands(self) -> Tuple[Hand, ...]:
        return self.result.hands

    @property
    def primary(self) -> Optional[Hand]:
        """Highest confidence hand, or None when nothing is tracked."""
        hands = self.result.hands
        if not hands:
            return None
        scored = [h for h in hands if h.confidence is not None]
        if not scored:
            return hands[0]
        return max(scored, key=lambda h: h.confidence or 0.0)


# --------------------------------------------------------------------------- #
# Landmark smoothing (One Euro filter)
# --------------------------------------------------------------------------- #


def _smoothing_alpha(cutoff: float, dt: float) -> float | np.ndarray:
    """Convert a cutoff frequency to an exponential smoothing factor."""
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class LandmarkSmoother:
    """One Euro filter over an ``(N, 3)`` landmark array.

    Jitter is removed aggressively while the hand is still, and almost none while
    it moves, which keeps the overlay attached to the hand without added lag.
    """

    def __init__(
        self,
        min_cutoff: float = 1.15,
        beta: float = 0.055,
        derivative_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.derivative_cutoff = derivative_cutoff
        self._filtered: Optional[np.ndarray] = None
        self._derivative: Optional[np.ndarray] = None

    def reset(self) -> None:
        """Drop filter history (called when a hand disappears or jumps)."""
        self._filtered = None
        self._derivative = None

    @property
    def is_warm(self) -> bool:
        return self._filtered is not None

    def apply(self, points: np.ndarray, dt: float) -> np.ndarray:
        """Filter a landmark array of shape ``(N, 3)`` and return a new array."""
        if points.size == 0:
            return points

        dt = min(max(dt, 1e-3), 0.25)
        previous = self._filtered
        if previous is None or previous.shape != points.shape:
            self._filtered = points.copy()
            self._derivative = np.zeros_like(points)
            return points.copy()

        raw_derivative = (points - previous) / dt
        derivative_alpha = _smoothing_alpha(self.derivative_cutoff, dt)
        derivative = derivative_alpha * raw_derivative + (1.0 - derivative_alpha) * self._derivative

        cutoff = self.min_cutoff + self.beta * np.abs(derivative)
        alpha = _smoothing_alpha(cutoff, dt)
        filtered = alpha * points + (1.0 - alpha) * previous

        self._filtered = filtered
        self._derivative = derivative
        return filtered


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #


@contextmanager
def _native_output_silenced() -> Iterator[None]:
    """Silence native (C++) stderr chatter while the MediaPipe graph starts up."""
    try:
        saved_stderr = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return

    try:
        os.dup2(devnull, 2)
        yield
    finally:
        try:
            os.dup2(saved_stderr, 2)
        finally:
            os.close(saved_stderr)
            os.close(devnull)


class HandTracker:
    """Threaded hand tracking pipeline built on MediaPipe hand landmarks.

    The inference worker always consumes the most recent submitted frame and
    discards stale ones, so the pipeline stays responsive even when inference is
    slower than the camera.
    """

    def __init__(
        self,
        max_hands: int = 1,
        model_complexity: int = 0,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        input_width: int = 640,
        smoothing: bool = True,
        lock_frames: int = 4,
        lost_grace_sec: float = 0.7,
        reset_hold_sec: float = 3.0,
    ) -> None:
        self.max_hands = max(1, int(max_hands))
        self.model_complexity = int(model_complexity)
        self.min_detection_confidence = float(min_detection_confidence)
        self.min_tracking_confidence = float(min_tracking_confidence)
        self.input_width = int(input_width)
        self.smoothing = bool(smoothing)
        self.lock_frames = max(1, int(lock_frames))
        self.lost_grace_sec = float(lost_grace_sec)
        self.reset_hold_sec = float(reset_hold_sec)

        self._lock = threading.Lock()
        self._frame_ready = threading.Event()
        self._pending_frame: Optional[np.ndarray] = None
        self._thread: Optional[threading.Thread] = None
        self._solution = None

        self._running = False
        self._ready = False
        self._init_error: Optional[str] = None

        self._state = TrackingState.NO_HAND
        self._result = HandTrackingResult.empty()
        self._stable_frames = 0
        self._last_seen = 0.0
        self._inference_ms = 0.0
        self._tracker_fps = 0.0
        self._dropped_frames = 0

        self._smoothers = [LandmarkSmoother() for _ in range(self.max_hands)]
        self._last_palm: Optional[Tuple[float, float]] = None
        self._last_dt = 1.0 / 30.0

    # -- lifecycle --------------------------------------------------------- #

    @property
    def is_ready(self) -> bool:
        """True once the landmark model is loaded and inference can run."""
        return self._ready

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def init_error(self) -> Optional[str]:
        """Human readable reason when the tracking engine could not start."""
        return self._init_error

    @property
    def state(self) -> TrackingState:
        return self._state

    def start(self) -> None:
        """Start the inference worker. Model loading happens off the main thread."""
        if self._running:
            return
        self._running = True
        self._init_error = None
        self._thread = threading.Thread(
            target=self._worker,
            name="VisionCoreHandTracking",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the worker and release the native landmark model."""
        if not self._running and self._thread is None:
            return
        self._running = False
        self._frame_ready.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._pending_frame = None
            self._ready = False

    def reset(self) -> None:
        """Forget all tracking history (used when the camera stream drops)."""
        with self._lock:
            self._pending_frame = None
            self._result = HandTrackingResult.empty(time.time())
        self._state = TrackingState.NO_HAND
        self._stable_frames = 0
        self._last_seen = 0.0
        self._last_palm = None
        self._inference_ms = 0.0
        self._tracker_fps = 0.0
        for smoother in self._smoothers:
            smoother.reset()

    # -- frame intake ------------------------------------------------------ #

    def submit_frame(self, frame: Optional[np.ndarray]) -> None:
        """Hand a fresh camera frame to the worker (never blocking, zero copy)."""
        if frame is None or frame.size == 0 or not self._running:
            return
        with self._lock:
            if self._pending_frame is not None:
                self._dropped_frames += 1
            self._pending_frame = frame
        self._frame_ready.set()

    def poll(self) -> TrackingSnapshot:
        """Return the current tracking snapshot, advancing time based states."""
        self._advance_timed_state()
        with self._lock:
            return TrackingSnapshot(
                state=self._state,
                result=self._result,
                inference_ms=self._inference_ms,
                tracker_fps=self._tracker_fps,
                dropped_frames=self._dropped_frames,
            )

    # -- worker ------------------------------------------------------------ #

    def _create_solution(self):
        """Instantiate the MediaPipe hand landmark graph (offline, no downloads)."""
        import mediapipe as mp  # imported lazily so the UI boots instantly

        return mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=self.max_hands,
            model_complexity=self.model_complexity,
            min_detection_confidence=self.min_detection_confidence,
            min_tracking_confidence=self.min_tracking_confidence,
        )

    def _worker(self) -> None:
        """Inference loop: latest frame in, structured landmarks out."""
        try:
            with _native_output_silenced():
                self._solution = self._create_solution()
        except Exception as exc:  # pragma: no cover - depends on host runtime
            self._init_error = f"{type(exc).__name__}: {exc}"
            self._running = False
            logger.error("Hand tracking engine failed to initialise: %s", self._init_error)
            return

        self._ready = True
        logger.info(
            "Hand tracking engine ready (max_hands=%d, model=%s, input_width=%d)",
            self.max_hands,
            "lite" if self.model_complexity == 0 else "full",
            self.input_width or 0,
        )

        last_frame_time = time.perf_counter()
        fps_window_start = last_frame_time
        fps_frames = 0

        while self._running:
            if not self._frame_ready.wait(timeout=0.1):
                continue
            self._frame_ready.clear()

            with self._lock:
                frame = self._pending_frame
                self._pending_frame = None
            if frame is None:
                continue

            prepared = self._prepare_frame(frame)
            started = time.perf_counter()
            try:
                raw = self._solution.process(prepared)
            except Exception as exc:  # pragma: no cover - native runtime failure
                logger.error("Hand tracking inference failed: %s", exc)
                continue
            now = time.perf_counter()
            inference_ms = (now - started) * 1000.0

            dt = now - last_frame_time
            last_frame_time = now
            self._last_dt = dt

            self._apply_state(bool(raw.multi_hand_landmarks), now)
            result = self._build_result(raw, inference_ms)

            fps_frames += 1
            if now - fps_window_start >= 0.5:
                self._tracker_fps = fps_frames / (now - fps_window_start)
                fps_frames = 0
                fps_window_start = now

            with self._lock:
                self._inference_ms = inference_ms
                self._result = result

        if self._solution is not None:
            try:
                self._solution.close()
            except Exception:  # pragma: no cover - native teardown
                pass
            self._solution = None
        self._ready = False
        logger.info("Hand tracking engine stopped.")

    def _prepare_frame(self, frame: np.ndarray) -> np.ndarray:
        """Downscale oversized frames to keep inference latency predictable."""
        width = frame.shape[1]
        if self.input_width <= 0 or width <= self.input_width:
            return frame

        scale = self.input_width / float(width)
        height = max(1, int(round(frame.shape[0] * scale)))
        return cv2.resize(frame, (self.input_width, height), interpolation=cv2.INTER_AREA)

    # -- result assembly --------------------------------------------------- #

    def _build_result(self, raw, inference_ms: float) -> HandTrackingResult:
        """Convert a MediaPipe result into the VisionCore tracking data model."""
        raw_hands = raw.multi_hand_landmarks or ()
        if not raw_hands:
            if self._last_palm is not None:
                for smoother in self._smoothers:
                    smoother.reset()
                self._last_palm = None
            return HandTrackingResult.empty(time.time(), inference_ms)

        handedness_list = raw.multi_handedness or ()
        hands = []

        for index, hand_landmarks in enumerate(raw_hands[: self.max_hands]):
            points = np.array(
                [(lm.x, lm.y, lm.z) for lm in hand_landmarks.landmark],
                dtype=np.float64,
            )

            if self.smoothing:
                points = self._smooth(index, points)

            handedness: Optional[str] = None
            confidence: Optional[float] = None
            if index < len(handedness_list):
                classification = handedness_list[index].classification
                if classification:
                    top = classification[0]
                    label = (top.label or "").strip().upper()
                    if label in ("LEFT", "RIGHT"):
                        # MediaPipe resolves handedness assuming a mirrored
                        # (selfie) image, which is exactly what the camera
                        # pipeline delivers, so the label needs no swapping.
                        handedness = label
                        confidence = float(top.score) if top.score is not None else None

            hands.append(
                Hand(
                    landmarks=tuple(Landmark(float(x), float(y), float(z)) for x, y, z in points),
                    handedness=handedness,
                    confidence=confidence,
                    bounding_box=self._bounding_region(points),
                    tracked=self._state is TrackingState.TRACKING,
                )
            )

        primary = hands[0]
        return HandTrackingResult(
            detected=True,
            hands=tuple(hands),
            landmarks=primary.landmarks,
            confidence=primary.confidence,
            handedness=primary.handedness,
            bounding_box=primary.bounding_box,
            timestamp=time.time(),
            inference_ms=inference_ms,
        )

    def _smooth(self, index: int, points: np.ndarray) -> np.ndarray:
        """Apply One Euro smoothing, restarting history if the hand jumps."""
        if index >= len(self._smoothers):
            return points

        smoother = self._smoothers[index]
        palm = (float(points[WRIST][0]), float(points[WRIST][1]))
        if self._last_palm is not None and index == 0:
            jump = math.hypot(palm[0] - self._last_palm[0], palm[1] - self._last_palm[1])
            if jump > 0.35:
                smoother.reset()
        if index == 0:
            self._last_palm = palm

        return smoother.apply(points, self._last_dt)

    @staticmethod
    def _bounding_region(points: np.ndarray) -> BoundingRegion:
        """Derive a normalised bounding region from raw landmarks."""
        xs = points[:, 0]
        ys = points[:, 1]
        pad = 0.035
        x0 = max(0.0, float(xs.min()) - pad)
        y0 = max(0.0, float(ys.min()) - pad)
        x1 = min(1.0, float(xs.max()) + pad)
        y1 = min(1.0, float(ys.max()) + pad)
        return BoundingRegion(x0, y0, x1 - x0, y1 - y0)

    # -- state machine ----------------------------------------------------- #

    def _apply_state(self, detected: bool, now: float) -> None:
        """Advance the tracking state machine after each inference pass."""
        if not detected:
            self._stable_frames = 0
            return

        self._stable_frames += 1
        self._last_seen = now

        if self._state in (TrackingState.NO_HAND, TrackingState.HAND_LOST):
            self._state = TrackingState.DETECTING
        elif self._state is TrackingState.DETECTING and self._stable_frames >= self.lock_frames:
            self._state = TrackingState.TRACKING

    def _advance_timed_state(self) -> None:
        """Apply time based transitions (lock grace, search reset)."""
        if self._state is TrackingState.NO_HAND or not self._last_seen:
            return

        age = time.perf_counter() - self._last_seen
        if self._state.is_engaged and age > self.lost_grace_sec:
            self._state = TrackingState.HAND_LOST
            logger.debug("Hand lost after %.2fs without detection", age)
        elif self._state is TrackingState.HAND_LOST and age > self.reset_hold_sec:
            self._state = TrackingState.NO_HAND
