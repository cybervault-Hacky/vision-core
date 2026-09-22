"""Central application coordinator and lifecycle orchestrator for VisionCore."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.camera import CameraManager
from app.config import AppConfig
from app.controls import (
    ControlMode,
    ControlState,
    DeviceAction,
    DeviceController,
    MouseController,
    WindowAction,
)
from app.gestures import GestureEngine
from app.hand_tracking import HandTracker, TrackingSnapshot
from app.logger import setup_logger
from app.state import AppState, SubsystemState, Telemetry
from ui.window import MainWindow
from utils.platform import PlatformInfo

logger = logging.getLogger("visioncore.app")

TRACKING_ENGINE_NAME = "MEDIAPIPE HANDS"


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

        logger.info("Initializing VisionCore...")

        # Telemetry model
        self.telemetry = Telemetry(
            app_state=AppState.BOOTING,
            camera_index=self.config.camera_index,
            mirrored=self.config.mirror_camera,
            max_hands=self.config.max_hands,
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

        # Hand tracking engine (local inference, runs on its own worker thread)
        self.tracker = HandTracker(
            max_hands=self.config.max_hands,
            model_complexity=self.config.tracking_model_complexity,
            min_detection_confidence=self.config.min_detection_confidence,
            min_tracking_confidence=self.config.min_tracking_confidence,
            input_width=self.config.tracking_input_width,
            smoothing=True,
        )
        self._tracking_announced = False
        self.telemetry.tracking = SubsystemState.DISABLED
        if self.config.tracking_enabled:
            self.telemetry.tracking = SubsystemState.INITIALIZING
            self.telemetry.tracking_engine = TRACKING_ENGINE_NAME

        # Gesture recognition engine (pure geometry over the tracked landmarks,
        # runs inline on the render thread and owns no external resources)
        self.gestures = GestureEngine(self.config.gesture_settings())
        self.telemetry.gestures = (
            SubsystemState.SEARCHING if self.gestures.enabled else SubsystemState.DISABLED
        )
        self._frame_aspect = (
            self.config.camera_width / self.config.camera_height
            if self.config.camera_height > 0
            else 4.0 / 3.0
        )
        self._gesture_snapshot = self.gestures.disabled_snapshot()
        self._gesture_now: Optional[float] = None

        # Touchless mouse control. The controller is created DISABLED: it never
        # moves the cursor until the user explicitly enables it from the HUD.
        self.mouse = MouseController(self.config.control_settings())
        self._control_snapshot = self.mouse.snapshot
        self.telemetry.update_control(self._control_snapshot)

        # Touchless device control: a separate layer with its own backend and its
        # own enable state, inert until the user enables it in DEVICE mode.
        self.device = DeviceController(
            self.config.device_settings(),
            gate_settings=self.config.control_settings(),
        )
        self._device_snapshot = self.device.snapshot
        self.telemetry.update_device(self._device_snapshot)

        # Control mode arbitration: one gesture can never act on both layers.
        self._control_mode = ControlMode.MOUSE
        self.mouse.set_mode(self._control_mode)
        self.device.set_mode(self._control_mode)
        self.telemetry.set_control_mode(self._control_mode)

        # UI Window Shell
        self.window = MainWindow(
            config=self.config,
            telemetry=self.telemetry,
            on_retry_camera=self._handle_camera_retry,
            on_exit=self.stop,
            on_control_toggle=self._toggle_mouse_control,
            on_control_disable=self._disable_mouse_control,
            on_control_mode=self._set_control_mode,
            on_device_toggle=self._toggle_device_control,
            on_device_action=self._device_action,
        )

        self._running = False
        self._camera_probe_thread: Optional[threading.Thread] = None
        self._camera_probe_complete = False
        self._camera_probe_success = False
        self._camera_probe_message = ""

    def _probe_camera_async(self) -> None:
        """Asynchronously probe camera hardware and warm the tracking engine."""
        logger.info("Probing camera hardware at index %d...", self.config.camera_index)
        success, msg = self.camera.start()
        self._camera_probe_success = success
        self._camera_probe_message = msg
        self._camera_probe_complete = True

        # Inform boot screen of status
        self.window.boot_screen.notify_camera_result(success, msg)

        if success:
            logger.info("Camera probe passed: %s", msg)
            if self.config.tracking_enabled:
                logger.info("Starting local hand tracking pipeline...")
                self.tracker.start()
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
            if self.config.tracking_enabled and not self.tracker.is_running:
                self.tracker.start()
            self.gestures.reset()
            self.device.on_tracking_lost()
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

    def _toggle_mouse_control(self) -> None:
        """UI action: enable, pause or resume the mouse control layer."""
        previous = self.mouse.state
        self.mouse.toggle()
        if previous is ControlState.DISABLED and self.mouse.state is ControlState.DISABLED:
            logger.warning("Mouse control could not be enabled: %s", self.mouse.message)
        self.telemetry.update_control(self.mouse.snapshot)

    def _disable_mouse_control(self) -> None:
        """UI action: disarm control and release everything it holds."""
        self.mouse.disable()
        self.telemetry.update_control(self.mouse.snapshot)

    def _set_control_mode(self, mode: ControlMode) -> None:
        """Switch between MOUSE and DEVICE control (explicit user action only)."""
        if mode is self._control_mode:
            return
        self._control_mode = mode
        self.mouse.set_mode(mode)
        self.device.set_mode(mode)
        self.telemetry.set_control_mode(mode)
        logger.info("Control mode set to %s", mode.label)

    def _toggle_device_control(self) -> None:
        """UI action: enable, pause or resume the device control layer."""
        self.device.toggle()
        self.telemetry.update_device(self.device.snapshot)

    def _device_action(self, action: DeviceAction, argument: Optional[str] = None) -> None:
        """UI action: run a window action or launch an allowlisted application."""
        if action is DeviceAction.LAUNCH_APP and argument:
            result = self.device.perform_launch(argument)
        else:
            window_action = {
                DeviceAction.MINIMIZE: WindowAction.MINIMIZE,
                DeviceAction.MAXIMIZE: WindowAction.MAXIMIZE,
                DeviceAction.NEXT_WINDOW: WindowAction.NEXT,
            }.get(action)
            if window_action is None:
                return
            result = self.device.perform_window_action(window_action)
        if not result.success:
            logger.info("Device action refused: %s (%s)", result.message, result.detail)
        self.telemetry.update_device(self.device.snapshot)

    def _sync_control(self, dt: float, tracking: TrackingSnapshot) -> None:
        """Run both control layers for one frame and publish their snapshots.

        The two layers are independent, but the emergency stop is shared: a stop
        raised by either of them immediately stops the other, so no action can
        survive the user's stop signal.
        """
        mouse_before = self.mouse.state
        device_before = self.device.state

        self._control_snapshot = self.mouse.update(dt, tracking, self._gesture_snapshot)
        self._device_snapshot = self.device.update(
            dt, tracking, self._gesture_snapshot, now=self._gesture_now
        )

        if (
            mouse_before is not ControlState.EMERGENCY_STOP
            and self.mouse.state is ControlState.EMERGENCY_STOP
        ):
            self.device.emergency_stop("EMERGENCY STOP")
            self._device_snapshot = self.device.snapshot
        elif (
            device_before is not ControlState.EMERGENCY_STOP
            and self.device.state is ControlState.EMERGENCY_STOP
        ):
            self.mouse.emergency_stop("EMERGENCY STOP")
            self._control_snapshot = self.mouse.snapshot

        self.telemetry.update_control(self._control_snapshot)
        self.telemetry.update_device(self._device_snapshot)

    def _sync_gestures(self, tracking: TrackingSnapshot) -> None:
        """Run one recognition pass per frame and publish it to the HUD."""
        if not self.gestures.enabled:
            return
        self._gesture_now = time.perf_counter()
        snapshot = self.gestures.process(tracking.result, self._frame_aspect, self._gesture_now)
        self.telemetry.update_gestures(snapshot)
        self._gesture_snapshot = snapshot

    def _sync_tracking(self, snapshot: TrackingSnapshot) -> None:
        """Publish measured tracking telemetry to the HUD (no simulated values)."""
        telemetry = self.telemetry

        if self.config.tracking_enabled and not self._tracking_announced:
            if self.tracker.init_error:
                self._tracking_announced = True
                telemetry.tracking = SubsystemState.UNAVAILABLE
                telemetry.tracking_engine = "UNAVAILABLE"
                self.window.boot_screen.notify_tracking_result(False, self.tracker.init_error)
                logger.warning("Hand tracking engine unavailable: %s", self.tracker.init_error)
            elif self.tracker.is_ready:
                self._tracking_announced = True
                self.window.boot_screen.notify_tracking_result(True, "Hand tracking online")
                logger.info("Hand tracking pipeline ready.")

        if self.tracker.is_ready:
            telemetry.set_tracking_state(snapshot.state)
            telemetry.tracking_engine = TRACKING_ENGINE_NAME
            primary = snapshot.primary
            telemetry.hands_detected = len(snapshot.hands)
            telemetry.hand_handedness = primary.handedness if primary else None
            telemetry.hand_confidence = primary.confidence if primary else None
            telemetry.tracker_fps = snapshot.tracker_fps
            telemetry.tracking_latency_ms = snapshot.inference_ms
            telemetry.tracking_dropped_frames = snapshot.dropped_frames

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

                        # Hand the frame to the tracking worker (zero copy, never blocking)
                        frame_height = frame.shape[0]
                        if frame_height > 0:
                            self._frame_aspect = frame.shape[1] / frame_height
                        self.tracker.submit_frame(frame)
                    elif not self.camera.is_active:
                        # Camera disconnected while active
                        logger.warning("Camera connection lost during active streaming")
                        self.tracker.reset()
                        self.gestures.reset()
                        self.telemetry.set_gestures_unavailable()
                        self.mouse.on_tracking_lost()
                        self.device.on_tracking_lost()
                        self.telemetry.update_control(self.mouse.snapshot)
                        self.telemetry.update_device(self.device.snapshot)
                        self.telemetry.set_camera_error(
                            title="CAMERA DISCONNECTED",
                            message="Camera video feed lost unexpectedly.",
                        )

                # 4. Synchronise tracking telemetry and render frame + HUD
                tracking = self.tracker.poll()
                self._sync_tracking(tracking)
                self._sync_gestures(tracking)
                self._sync_control(dt, tracking)
                self.window.render_frame(
                    current_frame,
                    dt,
                    tracking,
                    self._gesture_snapshot,
                    self._control_snapshot,
                    self._device_snapshot,
                )

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

        # Disarm device control first: release any held mouse button, stop the
        # pointer, cancel pending device actions and stop continuous volume or
        # brightness changes before anything else is torn down.
        try:
            self.mouse.close()
        except Exception as exc:
            logger.warning("Error shutting down mouse control: %s", exc)
        self.telemetry.set_control_unavailable()

        try:
            self.device.close()
        except Exception as exc:
            logger.warning("Error shutting down device control: %s", exc)
        self.telemetry.set_device_unavailable()

        # Release camera hardware
        try:
            self.camera.release()
        except Exception as exc:
            logger.warning("Error releasing camera during shutdown: %s", exc)

        # Release hand tracking engine
        try:
            self.tracker.close()
        except Exception as exc:
            logger.warning("Error stopping hand tracking engine: %s", exc)

        # Release gesture recognition state
        try:
            self.gestures.close()
        except Exception as exc:
            logger.warning("Error stopping gesture engine: %s", exc)
        self.telemetry.set_gestures_unavailable()

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
