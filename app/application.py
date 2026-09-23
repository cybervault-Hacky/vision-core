"""Central application coordinator and lifecycle orchestrator for VisionCore."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.ai import AIAssistant, ActionRouter, build_context, load_settings
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
from app.interaction import (
    Intent,
    IntentKind,
    IntentOutcome,
    IntentRouter,
    IntentSource,
    InteractionDirector,
    InteractionLifecycle,
    RecoveryAction,
    SystemError,
)
from app.logger import setup_logger
from app.state import AppState, SubsystemState, Telemetry
from ui.window import MainWindow
from utils.platform import PlatformInfo

logger = logging.getLogger("visioncore.app")

TRACKING_ENGINE_NAME = "MEDIAPIPE HANDS"

# Upper bound on the shutdown sequence so a display problem can never keep the
# process alive after control, the camera and the tracker have been released.
SHUTDOWN_SEQUENCE_LIMIT_SEC = 2.5


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

        # Interaction layer: one priority ordered intent router for every input
        # source, and the director that turns real subsystem state into the
        # interaction state the interface shows.
        self.intents = IntentRouter()
        self.director = InteractionDirector(
            confidence_threshold=self.config.safety_confidence_threshold,
            max_hands=self.config.max_hands,
        )
        self._register_intents()

        # VisionCore AI (Phase 7). The assistant is a coordinator that can only
        # propose intents from a closed allowlist; it is configured from the
        # environment, it contacts nothing until the user sends a message, and
        # with no provider it still answers state questions locally.
        self.ai_settings = load_settings()
        self.ai = AIAssistant(
            self.ai_settings,
            router=ActionRouter(
                dispatch=self._route_ai_intent,
                telemetry=lambda: self.telemetry,
            ),
            context_provider=lambda: build_context(self.telemetry),
            notify=self._notify_ai_result,
        )
        self._ai_snapshot = self.ai.snapshot()
        self.telemetry.update_ai(self._ai_snapshot)

        # UI Window Shell
        self.window = MainWindow(
            config=self.config,
            telemetry=self.telemetry,
            on_retry_camera=self._retry_camera,
            on_exit=self.stop,
            on_control_toggle=self._toggle_mouse_control,
            on_control_disable=self._disable_mouse_control,
            on_control_mode=self._set_control_mode,
            on_device_toggle=self._toggle_device_control,
            on_device_action=self._device_action,
            on_recovery=self._handle_recovery,
            on_ai_send=self._ai_send,
            on_ai_clear=self._ai_clear,
            on_ai_confirm=self._ai_confirm,
            on_ai_cancel=self._ai_cancel,
        )

        self._stopping = False
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
            self.director.clear_error(RecoveryAction.CAMERA)
            if self.config.tracking_enabled:
                logger.info("Starting local hand tracking pipeline...")
                self.tracker.start()
        else:
            logger.warning("Camera probe failed: %s", msg)
            self.director.report_error(
                SystemError(
                    "CAMERA UNAVAILABLE",
                    f"Index {self.config.camera_index}: {msg}",
                    RecoveryAction.CAMERA,
                )
            )

    # -- intent routes ----------------------------------------------------- #

    def _register_intents(self) -> None:
        """Bind every deliberate action to the layer that owns it.

        The router is the only place priority is decided, so a notification can
        never run ahead of a safety action and no route exists twice.
        """
        self.intents.register(IntentKind.EMERGENCY_STOP, self._intent_emergency_stop)
        self.intents.register(IntentKind.SAFETY_RESET, self._intent_control_toggle)
        self.intents.register(IntentKind.CONTROL_TOGGLE, self._intent_control_toggle)
        self.intents.register(IntentKind.CONTROL_DISABLE, self._intent_control_disable)
        self.intents.register(IntentKind.MODE_MOUSE, self._intent_mode_mouse)
        self.intents.register(IntentKind.MODE_DEVICE, self._intent_mode_device)
        self.intents.register(IntentKind.DEVICE_ACTION, self._intent_device_action)
        self.intents.register(IntentKind.RECOVERY, self._intent_recovery)

    def _dispatch(self, kind: IntentKind, label: str = "", payload: Optional[str] = None) -> bool:
        """Route one interface intent and log a refusal (never silently drop)."""
        outcome = self.intents.dispatch(
            Intent.create(
                kind,
                IntentSource.INTERFACE,
                label=label,
                payload=payload,
                timestamp=time.perf_counter(),
            )
        )
        if not outcome.accepted or not outcome.handled:
            logger.info(
                "Intent %s not applied (%s)",
                kind.value,
                outcome.reason or "no effect",
            )
        return outcome.accepted and outcome.handled

    def _intent_emergency_stop(self, intent: Intent) -> bool:
        """Highest priority route: release and stop both control layers."""
        self.mouse.emergency_stop(intent.label or "EMERGENCY STOP")
        self.device.emergency_stop(intent.label or "EMERGENCY STOP")
        self.telemetry.update_control(self.mouse.snapshot)
        self.telemetry.update_device(self.device.snapshot)
        return True

    def _intent_control_toggle(self, intent: Intent) -> bool:
        """Enable, pause or resume the mouse control layer."""
        previous = self.mouse.state
        self.mouse.toggle()
        if previous is ControlState.DISABLED and self.mouse.state is ControlState.DISABLED:
            logger.warning("Mouse control could not be enabled: %s", self.mouse.message)
            self.director.report_error(
                SystemError("MOUSE BACKEND UNAVAILABLE", self.mouse.message, RecoveryAction.CONTROL)
            )
        elif self.mouse.state.allows_actions:
            self.director.clear_error(RecoveryAction.CONTROL)
        self.telemetry.update_control(self.mouse.snapshot)
        return True

    def _intent_control_disable(self, intent: Intent) -> bool:
        """Disarm control and release everything it holds."""
        self.mouse.disable()
        self.telemetry.update_control(self.mouse.snapshot)
        return True

    def _intent_mode_mouse(self, intent: Intent) -> bool:
        return self._intent_set_mode(ControlMode.MOUSE)

    def _intent_mode_device(self, intent: Intent) -> bool:
        return self._intent_set_mode(ControlMode.DEVICE)

    def _intent_set_mode(self, mode: ControlMode) -> bool:
        """Switch between MOUSE and DEVICE control (explicit action only)."""
        if mode is self._control_mode:
            return False
        self._control_mode = mode
        self.mouse.set_mode(mode)
        self.device.set_mode(mode)
        self.telemetry.set_control_mode(mode)
        logger.info("Control mode set to %s", mode.label)
        return True

    def _intent_device_action(self, intent: Intent) -> bool:
        """Run a window action or launch an allowlisted application."""
        payload = intent.payload or ""
        if payload.startswith("LAUNCH:"):
            result = self.device.perform_launch(payload.split(":", 1)[1])
        else:
            window_action = {
                DeviceAction.MINIMIZE.value: WindowAction.MINIMIZE,
                DeviceAction.MAXIMIZE.value: WindowAction.MAXIMIZE,
                DeviceAction.NEXT_WINDOW.value: WindowAction.NEXT,
            }.get(payload)
            if window_action is not None:
                result = self.device.perform_window_action(window_action)
            else:
                # Any other device action (volume, media, brightness, window)
                # runs through the same controller entry point a gesture would
                # use, so the mode, enabled, safety and capability gates apply.
                action = _device_action_for(payload)
                if action is None:
                    return False
                result = self.device.run_action(action)
        if not result.success:
            logger.info("Device action refused: %s (%s)", result.message, result.detail)
        self.telemetry.update_device(self.device.snapshot)
        return result.success

    def _intent_recovery(self, intent: Intent) -> bool:
        """Run a real recovery attempt for the failing subsystem."""
        payload = intent.payload or ""
        if payload == RecoveryAction.CAMERA.value:
            return self._retry_camera()
        if payload == RecoveryAction.TRACKING.value:
            return self._retry_tracking()
        if payload == RecoveryAction.CONTROL.value:
            return self._intent_control_toggle(intent)
        return False

    # -- interface actions ------------------------------------------------- #

    def _toggle_mouse_control(self) -> None:
        """UI action: enable, pause or resume the mouse control layer."""
        state = self.mouse.state
        kind = (
            IntentKind.SAFETY_RESET
            if state in (ControlState.PAUSED, ControlState.EMERGENCY_STOP)
            else IntentKind.CONTROL_TOGGLE
        )
        self._dispatch(kind, label="CONTROL")

    def _disable_mouse_control(self) -> None:
        """UI action: disarm control and release everything it holds."""
        self._dispatch(IntentKind.CONTROL_DISABLE, label="CONTROL")

    def _set_control_mode(self, mode: ControlMode) -> None:
        """UI action: request a control mode change through the router."""
        kind = IntentKind.MODE_DEVICE if mode is ControlMode.DEVICE else IntentKind.MODE_MOUSE
        self._dispatch(kind, label=f"{mode.label} MODE")

    def _toggle_device_control(self) -> None:
        """UI action: enable, pause or resume the device control layer."""
        self.device.toggle()
        if self.device.state.allows_actions:
            self.director.clear_error(title="DEVICE CONTROL UNAVAILABLE")
        elif self.device.state is ControlState.DISABLED and not self.device.available:
            self.director.report_error(
                SystemError(
                    "DEVICE CONTROL UNAVAILABLE",
                    self.device.message or self.device.backend,
                    None,
                )
            )
        self.telemetry.update_device(self.device.snapshot)

    def _device_action(self, action: DeviceAction, argument: Optional[str] = None) -> None:
        """UI action: run a window action or launch an allowlisted application."""
        if action is DeviceAction.LAUNCH_APP and argument:
            payload = f"LAUNCH:{argument}"
        else:
            payload = action.value
        self._dispatch(IntentKind.DEVICE_ACTION, label=action.label, payload=payload)

    def _handle_recovery(self, recovery: RecoveryAction) -> None:
        """Retry button pressed inside the viewport."""
        self._dispatch(IntentKind.RECOVERY, label=recovery.label, payload=recovery.value)

    # -- assistant routes -------------------------------------------------- #

    def _route_ai_intent(self, intent: Intent) -> IntentOutcome:
        """Dispatch one AI intent through the same router and log a refusal.

        The assistant never calls a controller: it produces an intent with
        :class:`IntentSource.AI` and this route hands it to the one priority
        ordered router that every other input source already uses.
        """
        outcome = self.intents.dispatch(intent)
        if not outcome.accepted or not outcome.handled:
            logger.info(
                "AI intent %s not applied (%s)",
                intent.kind.value,
                outcome.reason or "no effect",
            )
        return outcome

    def _notify_ai_result(self, label: str, success: bool, detail: str = "") -> None:
        """Report an AI action through the Phase 6 notification system."""
        self.director.notify(label, success=success, detail=detail)

    def _ai_send(self, text: str) -> bool:
        """Send one typed message from the panel (never blocks the loop)."""
        return self.ai.send(text)

    def _ai_clear(self) -> None:
        """Clear the AI conversation (memory only)."""
        self.ai.clear()

    def _ai_confirm(self) -> None:
        """User pressed [ CONFIRM ] on a disruptive action."""
        self.ai.confirm()

    def _ai_cancel(self) -> None:
        """User pressed [ CANCEL ] on a disruptive action."""
        self.ai.cancel()

    def _update_ai(self, now: float) -> None:
        """Advance the assistant once per frame and publish its snapshot."""
        self.ai.update(now)
        self._ai_snapshot = self.ai.snapshot()
        self.telemetry.update_ai(self._ai_snapshot)

    # -- recovery ---------------------------------------------------------- #

    def _retry_camera(self) -> bool:
        """Re-probe camera hardware: the only recovery path for the camera."""
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
            self.director.clear_error(RecoveryAction.CAMERA)
            self.director.set_lifecycle(InteractionLifecycle.RUNNING)
            self._notify("CAMERA ONLINE")
            logger.info("Camera reconnected successfully!")
            return True

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
        self.director.set_lifecycle(InteractionLifecycle.ERROR)
        self.director.report_error(
            SystemError(
                "CAMERA UNAVAILABLE",
                f"Index {self.config.camera_index}: {msg}",
                RecoveryAction.CAMERA,
            )
        )
        self._notify("CAMERA STILL UNAVAILABLE", success=False, detail=f"INDEX {self.config.camera_index}")
        logger.warning("Camera retry failed: %s", msg)
        return False

    def _retry_tracking(self) -> bool:
        """Restart the local tracking worker after a failed initialisation."""
        if not self.config.tracking_enabled:
            self._notify("TRACKING DISABLED IN CONFIGURATION", success=False)
            return False
        if self.tracker.is_running:
            self._notify("TRACKING STARTUP IN PROGRESS")
            return True
        if self.tracker.is_ready:
            self._notify("TRACKING ALREADY ONLINE")
            return True

        logger.info("Retrying hand tracking engine start-up...")
        self.telemetry.tracking = SubsystemState.INITIALIZING
        self.telemetry.tracking_error = ""
        self._tracking_announced = False
        self.director.clear_error(RecoveryAction.TRACKING)
        self.tracker.start()
        self._notify("TRACKING RESTART REQUESTED")
        return True

    def _notify(self, label: str, success: bool = True, detail: str = "") -> None:
        """Report a real outcome through the visual feedback channel.

        This is presentation only: it never performs an action and is dropped
        while a safety state owns the notification.
        """
        self.director.notify(label, success=success, detail=detail)

    def _sync_control(self, dt: float, tracking: TrackingSnapshot, now: float) -> None:
        """Run both control layers for one frame and publish their snapshots.

        The two layers are independent, but the emergency stop is shared: a stop
        raised by either of them goes through the intent router, which holds it
        at the highest priority, so it can never be delayed or suppressed. The
        measured cost of the step is published for the diagnostics module.
        """
        started = time.perf_counter()
        mouse_before = self.mouse.state
        device_before = self.device.state

        # Both layers are driven by the same monotonic timestamp as the gesture
        # engine and the director, so timers (click cooldown, drag dwell, open
        # palm stop) cannot drift apart between subsystems.
        self._control_snapshot = self.mouse.update(
            dt, tracking, self._gesture_snapshot, now=now
        )
        self._device_snapshot = self.device.update(
            dt, tracking, self._gesture_snapshot, now=now
        )

        mouse_stopped = (
            mouse_before is not ControlState.EMERGENCY_STOP
            and self.mouse.state is ControlState.EMERGENCY_STOP
        )
        device_stopped = (
            device_before is not ControlState.EMERGENCY_STOP
            and self.device.state is ControlState.EMERGENCY_STOP
        )
        if mouse_stopped or device_stopped:
            self.intents.dispatch(
                Intent.create(
                    IntentKind.EMERGENCY_STOP,
                    IntentSource.GESTURE,
                    label=self.mouse.message or self.device.message or "EMERGENCY STOP",
                    timestamp=now,
                )
            )
            self._control_snapshot = self.mouse.snapshot
            self._device_snapshot = self.device.snapshot

        self.telemetry.update_control(self._control_snapshot)
        self.telemetry.update_device(self._device_snapshot)

        # Measured cost of the control layer for this frame (real value, shown by
        # the diagnostics module; 0 means "not measured yet").
        self.telemetry.control_latency_ms = (time.perf_counter() - started) * 1000.0

        # Keep the router's safety posture in step with the real layer states.
        self.intents.set_safety(
            emergency=(
                self.mouse.state is ControlState.EMERGENCY_STOP
                or self.device.state is ControlState.EMERGENCY_STOP
            ),
            paused=(
                self.mouse.state is ControlState.PAUSED
                or self.device.state is ControlState.PAUSED
            ),
        )

    def _sync_gestures(self, tracking: TrackingSnapshot, now: float) -> None:
        """Run one recognition pass per frame and publish it to the HUD."""
        if not self.gestures.enabled:
            return
        snapshot = self.gestures.process(tracking.result, self._frame_aspect, now)
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
                telemetry.tracking_error = self.tracker.init_error
                self.window.boot_screen.notify_tracking_result(False, self.tracker.init_error)
                self.director.report_error(
                    SystemError(
                        "TRACKING ENGINE UNAVAILABLE",
                        self.tracker.init_error,
                        RecoveryAction.TRACKING,
                    )
                )
                logger.warning("Hand tracking engine unavailable: %s", self.tracker.init_error)
            elif self.tracker.is_ready:
                self._tracking_announced = True
                telemetry.tracking_error = ""
                self.window.boot_screen.notify_tracking_result(True, "Hand tracking online")
                self.director.clear_error(RecoveryAction.TRACKING)
                logger.info("Hand tracking pipeline ready.")

        # The interaction layer only needs to know whether a pipeline is live.
        self.director.set_tracking_available(
            self.config.tracking_enabled and self.tracker.init_error is None
        )

        if self.tracker.is_ready:
            telemetry.set_tracking_state(snapshot.state)
            telemetry.tracking_engine = TRACKING_ENGINE_NAME
            primary = snapshot.primary
            telemetry.hands_detected = len(snapshot.hands)
            telemetry.hand_handedness = primary.handedness if primary else None
            telemetry.hand_confidence = primary.confidence if primary else None
            telemetry.tracker_lock_progress = snapshot.lock_progress
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

        # Report the two subsystems that need no asynchronous probe: the gesture
        # engine and the control layer both know their state at start-up.
        self.window.boot_screen.notify_gesture_result(self.gestures.enabled)
        control_ready = self.config.mouse_control_enabled and self.mouse.available
        self.window.boot_screen.notify_control_result(control_ready, self.mouse.message)

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
                if not self._running:
                    # A UI action (for example [ EXIT SYSTEM ]) already shut the
                    # application down: never render onto a closed display.
                    break

                # 1b. Advance the AI assistant. This drains results from its
                # background worker and never blocks: a slow provider costs the
                # render loop nothing.
                self._update_ai(time.perf_counter())

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
                            self.director.set_lifecycle(InteractionLifecycle.RUNNING)
                            self.director.clear_error(RecoveryAction.CAMERA)
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
                            self.director.set_lifecycle(InteractionLifecycle.ERROR)
                            self.director.report_error(
                                SystemError(
                                    "CAMERA UNAVAILABLE",
                                    f"Index {self.config.camera_index}: {self._camera_probe_message}",
                                    RecoveryAction.CAMERA,
                                )
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
                        self.director.set_lifecycle(InteractionLifecycle.ERROR)
                        self.director.report_error(
                            SystemError(
                                "CAMERA UNAVAILABLE",
                                "Camera video feed lost unexpectedly.",
                                RecoveryAction.CAMERA,
                            )
                        )

                # 4. Synchronise tracking telemetry and render frame + HUD.
                # One monotonic timestamp per frame keeps every layer, timer and
                # animation on the same timebase.
                frame_now = time.perf_counter()
                tracking = self.tracker.poll()
                self._sync_tracking(tracking)
                self._sync_gestures(tracking, frame_now)
                self._sync_control(dt, tracking, frame_now)
                self.director.update(dt, self.telemetry, now=frame_now)
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
        """Release every resource in safety order, then show the shutdown sequence.

        Ordering is not cosmetic: control is released (including any held mouse
        button), the device layer is disabled, the tracker is stopped and the
        camera is closed *before* any shutdown visual is drawn, so the animation
        can never delay or mask a safety action.
        """
        if self._stopping or self.telemetry.app_state is AppState.STOPPED:
            return
        self._stopping = True

        logger.info("Shutting down VisionCore...")
        self._running = False
        self.telemetry.app_state = AppState.SHUTTING_DOWN
        self.director.set_lifecycle(InteractionLifecycle.SHUTTING_DOWN)

        # 0. Stop the assistant first: it must not hold an open request while
        # the application is shutting down. Any pending answer is discarded.
        try:
            self.ai.close()
        except Exception as exc:
            logger.warning("Error stopping the AI assistant: %s", exc)

        # 1. Release mouse control: any held button first, then the backend.
        control_released = False
        try:
            self.mouse.close()
            # The layer is only considered released when it is disarmed *and*
            # no button is still held down.
            control_released = (
                self.mouse.state is ControlState.DISABLED
                and not self.mouse.snapshot.dragging
            )
        except Exception as exc:
            logger.warning("Error shutting down mouse control: %s", exc)
        self.telemetry.set_control_unavailable()

        # 2. Stop device actions (and cancel continuous volume/brightness).
        device_released = False
        try:
            self.device.close()
            device_released = self.device.state is ControlState.DISABLED
        except Exception as exc:
            logger.warning("Error shutting down device control: %s", exc)
        self.telemetry.set_device_unavailable()

        # 3. Stop the tracking engine before the camera is taken away from it.
        tracking_stopped = False
        try:
            self.tracker.close()
            tracking_stopped = not self.tracker.is_running
        except Exception as exc:
            logger.warning("Error stopping hand tracking engine: %s", exc)

        # 4. Release the camera.
        camera_off = False
        try:
            self.camera.release()
            camera_off = not self.camera.is_active
        except Exception as exc:
            logger.warning("Error releasing camera during shutdown: %s", exc)

        # 5. Drop gesture recognition state.
        try:
            self.gestures.close()
        except Exception as exc:
            logger.warning("Error stopping gesture engine: %s", exc)
        self.telemetry.set_gestures_unavailable()

        # 6. Only now: show what actually happened, then close the window.
        self._render_shutdown_sequence(
            control_ok=control_released and device_released,
            tracking_ok=tracking_stopped,
            camera_ok=camera_off,
        )

        try:
            self.window.close()
        except Exception as exc:
            logger.warning("Error closing window during shutdown: %s", exc)

        try:
            pygame.quit()
        except Exception:
            pass

        # The recent-action timeline is memory only: it dies with the process.
        self.director.feedback.clear()
        self._stopping = False
        self.telemetry.app_state = AppState.STOPPED
        logger.info("VisionCore stopped cleanly.")

    def _render_shutdown_sequence(
        self,
        control_ok: bool,
        tracking_ok: bool,
        camera_ok: bool,
    ) -> None:
        """Draw the shutdown sequence from the results of the cleanup above.

        Every status shown here was already achieved; the loop is bounded and the
        user can dismiss it with any key or click. A display problem is caught
        and ignored - cleanup has already happened and must never be undone.
        """
        if self.config.tracking_enabled:
            tracking_status = "STOPPED" if tracking_ok else "ERROR"
        else:
            tracking_status = "DISABLED"

        steps = [
            ("RELEASING CONTROL", "RELEASED" if control_ok else "ERROR", control_ok),
            ("STOPPING TRACKING", tracking_status, tracking_ok or not self.config.tracking_enabled),
            ("CAMERA OFF", "OFF" if camera_ok else "ERROR", camera_ok),
            ("SYSTEM IDLE", "IDLE", True),
        ]

        try:
            self.window.shutdown_screen.start(steps)
            clock = self.window.clock
            deadline = time.monotonic() + SHUTDOWN_SEQUENCE_LIMIT_SEC
            last = time.monotonic()
            while time.monotonic() < deadline:
                for event in pygame.event.get():
                    if event.type in (
                        pygame.QUIT,
                        pygame.KEYDOWN,
                        pygame.MOUSEBUTTONDOWN,
                    ):
                        logger.debug("Shutdown sequence dismissed by the user")
                        return
                now = time.monotonic()
                dt = max(0.001, min(0.1, now - last))
                last = now
                if self.window.render_shutdown(dt):
                    return
                clock.tick(60)
        except Exception as exc:
            logger.warning("Shutdown sequence could not be displayed: %s", exc)


def _device_action_for(payload: str) -> Optional[DeviceAction]:
    """Map an intent payload onto an allowlisted device action (or None)."""
    try:
        action = DeviceAction(payload)
    except ValueError:
        return None
    if action in (DeviceAction.LAUNCH_APP,):
        # Launching an application is not part of the assistant's vocabulary.
        return None
    return action
