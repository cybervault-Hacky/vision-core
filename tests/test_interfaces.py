"""Unit tests verifying architectural interfaces and contracts for future phases."""

from __future__ import annotations

import numpy as np
import pytest

from app.interfaces import (
    GestureEvent,
    GestureType,
    IDeviceController,
    IGestureClassifier,
    ITrackingPipeline,
    NormalizedLandmark,
    TrackingResult,
)


def test_normalized_landmark_structure():
    lm = NormalizedLandmark(x=0.5, y=0.5, z=0.1, visibility=0.95)
    assert lm.x == 0.5
    assert lm.y == 0.5
    assert lm.z == 0.1
    assert lm.visibility == 0.95


def test_tracking_result_structure():
    res = TrackingResult(
        landmarks=[NormalizedLandmark(x=0.1, y=0.2)],
        handedness="Right",
        confidence=0.88,
    )
    assert len(res.landmarks) == 1
    assert res.handedness == "Right"
    assert res.confidence == 0.88


def test_gesture_event_structure():
    event = GestureEvent(
        gesture=GestureType.PINCH,
        confidence=0.92,
        origin=(0.4, 0.6),
    )
    assert event.gesture == GestureType.PINCH
    assert event.confidence == 0.92
    assert event.origin == (0.4, 0.6)


def test_interface_unimplemented_in_phase_1():
    """Verify interfaces raise NotImplementedError in Phase 1 as expected."""
    class DummyTracker(ITrackingPipeline):
        def initialize(self) -> bool:
            return super().initialize()

        def process(self, frame: np.ndarray):
            return super().process(frame)

        def release(self) -> None:
            return super().release()

    class DummyClassifier(IGestureClassifier):
        def classify(self, tracking_results):
            return super().classify(tracking_results)

    class DummyController(IDeviceController):
        def dispatch(self, event):
            return super().dispatch(event)

    tracker = DummyTracker()
    classifier = DummyClassifier()
    controller = DummyController()

    with pytest.raises(NotImplementedError):
        tracker.initialize()
    with pytest.raises(NotImplementedError):
        tracker.process(np.zeros((10, 10, 3), dtype=np.uint8))
    with pytest.raises(NotImplementedError):
        tracker.release()
    with pytest.raises(NotImplementedError):
        classifier.classify([])
    with pytest.raises(NotImplementedError):
        controller.dispatch(GestureEvent())
