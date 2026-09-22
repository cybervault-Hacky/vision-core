"""Unit tests for CameraManager and video capture lifecycle."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.camera import CameraManager, MockCameraSource


class TestMockCameraSource:
    """Validate synthetic calibration camera generator."""

    def test_mock_camera_stream(self):
        source = MockCameraSource(width=320, height=240, fps=30)
        assert source.isOpened() is True

        ret, frame = source.read()
        assert ret is True
        assert frame is not None
        assert frame.shape == (240, 320, 3)
        assert source.frame_count == 1

        source.release()
        assert source.isOpened() is False
        ret_after, frame_after = source.read()
        assert ret_after is False
        assert frame_after is None


class TestCameraManager:
    """Validate camera initialization, frame processing, and failure modes."""

    def test_mock_camera_initialization(self):
        manager = CameraManager(camera_index=0, mirror=True, mock_mode=True)
        success, message = manager.start()

        assert success is True
        assert "online" in message.lower()
        assert manager.is_active is True
        assert manager.width > 0
        assert manager.height > 0

        # Wait briefly for worker thread to process at least one frame
        time.sleep(0.05)

        has_frame, frame, fps = manager.get_frame()
        assert has_frame is True
        assert frame is not None
        assert isinstance(frame, np.ndarray)
        assert len(frame.shape) == 3

        manager.release()
        assert manager.is_active is False
        has_frame_after, frame_after, _ = manager.get_frame()
        assert has_frame_after is False
        assert frame_after is None

    def test_camera_unavailable_failure(self):
        """Simulate camera device that cannot be opened."""
        manager = CameraManager(camera_index=999, mock_mode=False)

        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = False
            mock_cap_cls.return_value = mock_cap

            success, message = manager.start()
            assert success is False
            assert "failed to open" in message.lower()
            assert manager.is_active is False

    def test_camera_frame_read_failure_on_startup(self):
        """Simulate camera that opens but fails to read validation frames."""
        manager = CameraManager(camera_index=0, mock_mode=False)

        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.read.return_value = (False, None)
            mock_cap_cls.return_value = mock_cap

            success, message = manager.start()
            assert success is False
            assert "returned no valid frames" in message.lower()
            assert manager.is_active is False

    def test_clean_release_and_cleanup(self):
        """Verify camera resources and capture threads are stopped and joined."""
        manager = CameraManager(mock_mode=True)
        manager.start()
        assert manager.is_active is True

        manager.stop()
        assert manager.is_active is False

        # Calling release multiple times should be idempotent and safe
        manager.release()
        manager.release()
        assert manager.is_active is False
        assert manager._capture is None

    def test_probe_unavailable_camera(self):
        """Probe method should return False without raising exceptions."""
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = False
            mock_cap_cls.return_value = mock_cap

            available, msg, info = CameraManager.probe(camera_index=88)
            assert available is False
            assert "could not be opened" in msg
            assert info["index"] == 88

    def test_probe_successful_camera(self):
        """Probe method should return True and resolution telemetry when camera works."""
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            sample_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            mock_cap.read.return_value = (True, sample_frame)
            mock_cap.get.side_effect = lambda prop: 640.0 if prop == 3 else 480.0 if prop == 4 else 30.0
            mock_cap.getBackendName.return_value = "V4L2"
            mock_cap_cls.return_value = mock_cap

            available, msg, info = CameraManager.probe(camera_index=0)
            assert available is True
            assert info["width"] == 640
            assert info["height"] == 480
            assert info["fps"] == 30.0
            assert info["backend"] == "V4L2"
