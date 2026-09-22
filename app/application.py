"""Central application coordinator and lifecycle orchestrator for VisionCore."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Optional

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.camera import CameraManager
from app.config import AppConfig
from app.logger import setup_logger
from app.state import AppState, SubsystemState, Telemetry
from ui.window import MainWindow
from utils.diagnostics import SystemDiagnostics
from utils.platform import PlatformInfo

logger = logging.getLogger("visioncore.app")


class Application:
    """Coordinates lifecycle, subsystems, event loop, and state transitions."""

    def __init__(self, config: Optional[AppConfig] = None):
        # Suppress pygame welcome banner
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

        self.config = config or AppConfig.load()
        self.config.validate()

        # Initialize logging
        setup_logger(
            name="visioncore",
            level=self.config.log_level,
            log_file="visioncore.log" if self.config.show_debug else None,
        )

        logger.info("Initializing VisionCore (Phase 1 Foundation)...")

        # Telemetry model
        self.telemetry = Telemetry(
            app_state=AppState.BOOTING,
            camera_index=self.config.camera_index,
            mirrored=self.config.mirror_camera,
        )

        # Platform analysis
        self.platform_info = PlatformInfo.current()
        logger.info(
            "Host: %s %s (%s) | Display: %s",
            self.platform_info.system,
            self.platform_info.release,
            self.platform_info.machine,
            self.platform_info.display_server,
        )

        # Camera manager
        self.camera = CameraManager(
            camera_index=self.config.camera_index,
            mirror=self.config.mirror_camera,
            target_fps=self.config.target_fps,
            mock_mode=self.config.mock_camera,
        )

        # UI Window Shell
        self.window = MainWindow(
            config=self.config,
            telemetry=self.telemetry,
            on_retry_camera=self._handle_camera_retry,
            on_exit=self.stop,
        )

        self._running = False
        self._camera_probe_thread: Optional[threading.Thread] = None
        self._camera_probe_complete = False
        self._camera_probe_success = False
        self._camera_probe_message = ""

    def _probe_camera_async(self) -> None:
        """Asynchronously probe and initialize camera hardware during boot."""
        logger.info("Probing camera hardware at index %d...", self.config.camera_index)
        success, msg = self.camera.start()
        self._camera_probe_success = success
        self._camera_probe_message = msg
        self._camera_probe_complete = True

        # Inform boot screen of status
        self.window.boot_screen.notify_camera_result(success, msg)

        if success:
            logger.info("Camera probe passed: %s", msg)
        else:
            logger.warning("Camera probe failed: %s", msg)

    def _handle_camera_retry(self) -> None:
        """Re-probe camera when user presses Retry button on error screen."""
        logger.info("Attempting camera reconnection retry...")
        self.camera.release()
        success, msg = self.camera.start()
        if success:
            self.telemetry.set_camera_online(
                width=self.camera.width,
                height=self.camera.height,
                fps=self.camera.hardware_fps,
                backend=self.camera.backend,
            )
            logger.info("Camera reconnected successfully!")
        else:
            hint = self.platform_info.camera_permission_hint()
            self.telemetry.set_camera_error(
                title="CAMERA RECONNECTION FAILED",
                message=f"Unable to access camera index {self.config.camera_index}: {msg}",
                instructions=[
                    "Check device physical USB cable or power connection.",
                    hint,
                    "Ensure no other app is actively streaming from this camera.",
                ],
            )
            logger.warning("Camera retry failed: %s", msg)

    def run(self) -> int:
        """Execute the primary desktop application loop."""
        logger.info("VisionCore application loop started.")
        self._running = True

        # Launch non-blocking camera probe during boot
        self._camera_probe_thread = threading.Thread(
            target=self._probe_camera_async,
            name="VisionCoreCameraProbe",
            daemon=True,
        )
        self._camera_probe_thread.start()

        last_time = time.time()
        fps_frame_count = 0
        fps_calc_time = time.time()

        try:
            while self._running:
                now = time.time()
                dt = max(0.0001, min(0.1, now - last_time))
                last_time = now

                # 1. Process Window Events
                if not self.window.handle_events():
                    logger.info("Termination requested via window event")
                    break

                # 2. State Progression
                if self.telemetry.app_state == AppState.BOOTING:
                    # Boot screen update returns True when boot animation finishes
                    boot_finished = self.window.boot_screen.update(dt)

                    if boot_finished:
                        # Wait briefly for probe thread if still running
                        if self._camera_probe_thread and self._camera_probe_thread.is_alive():
                            self._camera_probe_thread.join(timeout=0.5)

                        if self._camera_probe_success:
                            self.telemetry.set_camera_online(
                                width=self.camera.width,
                                height=self.camera.height,
                                fps=self.camera.hardware_fps,
                                backend=self.camera.backend,
                            )
                            logger.info("Boot sequence complete. Transitioning to CAMERA_ACTIVE.")
                        else:
                            hint = self.platform_info.camera_permission_hint()
                            self.telemetry.set_camera_error(
                                title="CAMERA UNAVAILABLE",
                                message=f"No usable camera could be initialized at index {self.config.camera_index}.",
                                instructions=[
                                    "Check that your camera is connected and powered.",
                                    hint,
                                    "Ensure another application is not exclusively using it.",
                                ],
                            )
                            logger.warning(
                                "Boot sequence complete. Transitioning to CAMERA_ERROR: %s",
                                self._camera_probe_message,
                            )

                # 3. Retrieve Latest Frame if Active
                current_frame = None
                if self.telemetry.app_state == AppState.CAMERA_ACTIVE:
                    has_frame, frame, capture_fps = self.camera.get_frame()
                    if has_frame and frame is not None:
                        current_frame = frame
                        self.telemetry.camera_fps = capture_fps
                        self.telemetry.frame_count += 1
                        if self.camera.width > 0 and self.telemetry.camera_width == 0:
                            self.telemetry.camera_width = self.camera.width
                            self.telemetry.camera_height = self.camera.height
                    elif not self.camera.is_active:
                        # Camera disconnected while active
                        logger.warning("Camera connection lost during active streaming")
                        self.telemetry.set_camera_error(
                            title="CAMERA DISCONNECTED",
                            message="Camera video feed lost unexpectedly.",
                        )

                # 4. Render Frame & HUD
                self.window.render_frame(current_frame, dt)

                # 5. Measure and Update Render FPS
                fps_frame_count += 1
                if now - fps_calc_time >= 1.0:
                    self.telemetry.render_fps = fps_frame_count / (now - fps_calc_time)
                    fps_frame_count = 0
                    fps_calc_time = now

                # 6. Pace loop to target frame rate
                self.window.clock.tick(self.config.target_fps)

        except KeyboardInterrupt:
            logger.info("VisionCore interrupted by user (KeyboardInterrupt)")
        except Exception as exc:
            logger.error("Unhandled exception in application loop: %s", exc, exc_info=True)
            return 1
        finally:
            self.stop()

        return 0

    def stop(self) -> None:
        """Execute a clean, graceful shutdown releasing all hardware and window resources."""
        if not self._running and self.telemetry.app_state == AppState.STOPPED:
            return

        logger.info("Shutting down VisionCore...")
        self._running = False
        self.telemetry.app_state = AppState.SHUTTING_DOWN

        # Release camera hardware
        try:
            self.camera.release()
        except Exception as exc:
            logger.warning("Error releasing camera during shutdown: %s", exc)

        # Close window & pygame display
        try:
            self.window.close()
        except Exception as exc:
            logger.warning("Error closing window during shutdown: %s", exc)

        try:
            pygame.quit()
        except Exception:
            pass

        self.telemetry.app_state = AppState.STOPPED
        logger.info("VisionCore stopped cleanly.")
