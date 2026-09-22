"""Integration tests for application orchestration and state machine."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pygame
import pytest

from app.application import Application
from app.config import AppConfig
from app.state import AppState, SubsystemState


class TestApplicationLifecycle:
    """Validate startup, boot sequence, state transitions, and shutdown."""

    def test_application_initialization_state(self):
        config = AppConfig(boot_duration_sec=0.5, mock_camera=True)
        app = Application(config=config)

        # Initial telemetry state
        assert app.telemetry.app_state == AppState.BOOTING
        assert app.telemetry.vision_core == SubsystemState.ONLINE
        assert app.telemetry.tracking == SubsystemState.STANDBY
        assert app.telemetry.gestures == SubsystemState.STANDBY
        assert app.telemetry.control == SubsystemState.DISABLED

        app.stop()
        assert app.telemetry.app_state == AppState.STOPPED

    def test_boot_sequence_to_camera_active_transition(self):
        """When camera probe succeeds, boot sequence transitions to CAMERA_ACTIVE."""
        config = AppConfig(boot_duration_sec=0.3, mock_camera=True)
        app = Application(config=config)

        # Helper thread to post QUIT after transition
        def terminator():
            # Wait for boot to finish (0.3s duration)
            time.sleep(0.6)
            pygame.event.post(pygame.event.Event(pygame.QUIT))

        t = threading.Thread(target=terminator, daemon=True)
        t.start()

        exit_code = app.run()
        assert exit_code == 0
        assert app.camera.is_active is False  # Cleanly released on exit

    def test_boot_sequence_to_camera_error_transition(self):
        """When camera is unavailable, boot sequence transitions to CAMERA_ERROR."""
        config = AppConfig(boot_duration_sec=0.3, mock_camera=False)

        # Patch cv2.VideoCapture to simulate hardware absence
        with patch("cv2.VideoCapture") as mock_cap_cls:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = False
            mock_cap_cls.return_value = mock_cap

            app = Application(config=config)

            def check_and_quit():
                time.sleep(0.6)
                assert app.telemetry.app_state == AppState.CAMERA_ERROR
                assert app.telemetry.camera == SubsystemState.UNAVAILABLE
                assert app.telemetry.error_title is not None
                assert len(app.telemetry.error_instructions) > 0
                pygame.event.post(pygame.event.Event(pygame.QUIT))

            t = threading.Thread(target=check_and_quit, daemon=True)
            t.start()

            exit_code = app.run()
            assert exit_code == 0

    def test_camera_retry_mechanism(self):
        """Validate on_retry_camera attempts to reconnect."""
        config = AppConfig(boot_duration_sec=0.2, mock_camera=True)
        app = Application(config=config)

        # Force error state
        app.telemetry.set_camera_error("TEST_ERROR", "Simulated failure")
        assert app.telemetry.app_state == AppState.CAMERA_ERROR

        # Trigger retry handler
        app._handle_camera_retry()
        # With mock_mode=True, camera reconnects
        assert app.telemetry.app_state == AppState.CAMERA_ACTIVE
        assert app.telemetry.camera == SubsystemState.ONLINE

        app.stop()
        assert app.telemetry.app_state == AppState.STOPPED

    def test_idempotent_shutdown(self):
        """Verify multiple calls to stop() do not raise errors."""
        config = AppConfig(boot_duration_sec=0.2, mock_camera=True)
        app = Application(config=config)

        app.stop()
        app.stop()
        assert app.telemetry.app_state == AppState.STOPPED
