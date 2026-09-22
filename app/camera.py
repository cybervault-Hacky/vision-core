"""Camera hardware capture engine and stream manager for VisionCore."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("visioncore.camera")


class MockCameraSource:
    """Synthetic calibration pattern generator for headless environments."""

    def __init__(self, width: int = 640, height: int = 480, fps: int = 30):
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = 0
        self.is_opened = True
        self._start_time = time.time()

    def isOpened(self) -> bool:
        return self.is_opened

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.is_opened:
            return False, None

        self.frame_count += 1
        elapsed = time.time() - self._start_time

        # Create a sci-fi synthetic video pattern
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Subtle dark gradient background
        y_indices = np.linspace(15, 35, self.height, dtype=np.uint8)[:, None]
        frame[:, :, 0] = y_indices  # Blue channel gradient
        frame[:, :, 1] = y_indices // 2
        frame[:, :, 2] = y_indices // 3

        # Grid lines
        grid_spacing = 40
        for x in range(0, self.width, grid_spacing):
            cv2.line(frame, (x, 0), (x, self.height), (35, 55, 75), 1)
        for y in range(0, self.height, grid_spacing):
            cv2.line(frame, (0, y), (self.width, y), (35, 55, 75), 1)

        # Animated circular radar pulse in center
        cx, cy = self.width // 2, self.height // 2
        radius = int(50 + 40 * np.sin(elapsed * 2.5))
        cv2.circle(frame, (cx, cy), radius, (0, 200, 220), 2)
        cv2.circle(frame, (cx, cy), 8, (0, 255, 255), -1)

        # Crosshairs
        cv2.line(frame, (cx - radius - 15, cy), (cx + radius + 15, cy), (0, 150, 180), 1)
        cv2.line(frame, (cx, cy - radius - 15), (cx, cy + radius + 15), (0, 150, 180), 1)

        # Moving orbital indicator
        orbit_angle = elapsed * 1.5
        ox = int(cx + (radius + 20) * np.cos(orbit_angle))
        oy = int(cy + (radius + 20) * np.sin(orbit_angle))
        cv2.circle(frame, (ox, oy), 5, (0, 255, 120), -1)

        # Technical watermark
        cv2.putText(
            frame,
            "VISIONCORE // SYNTHETIC CALIBRATION STREAM",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 220, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"FRAME: {self.frame_count:06d}  |  TIME: {elapsed:.2f}s",
            (20, self.height - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (180, 200, 220),
            1,
            cv2.LINE_AA,
        )

        return True, frame

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        if prop_id == cv2.CAP_PROP_FPS:
            return float(self.fps)
        return 0.0

    def release(self) -> None:
        self.is_opened = False


class CameraManager:
    """Manages camera lifecycle, asynchronous frame capture, and preprocessing."""

    def __init__(
        self,
        camera_index: int = 0,
        mirror: bool = True,
        target_fps: int = 30,
        mock_mode: bool = False,
    ):
        self.camera_index = camera_index
        self.mirror = mirror
        self.target_fps = target_fps
        self.mock_mode = mock_mode

        self._capture: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

        # Telemetry & metrics
        self._latest_frame: Optional[np.ndarray] = None
        self._actual_fps: float = 0.0
        self._fps_window: list[float] = []

        self.width: int = 0
        self.height: int = 0
        self.hardware_fps: float = 0.0
        self.backend: str = "UNKNOWN"

    @property
    def is_active(self) -> bool:
        """Indicate whether the capture loop is actively running and healthy."""
        with self._lock:
            return self._running and self._capture is not None

    @classmethod
    def probe(cls, camera_index: int = 0) -> Tuple[bool, str, Dict[str, Any]]:
        """Non-destructively test camera availability and read capabilities."""
        info: Dict[str, Any] = {
            "index": camera_index,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "backend": "UNKNOWN",
        }

        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return False, f"Device at index {camera_index} could not be opened", info

            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                cap.release()
                return False, f"Device at index {camera_index} opened but failed to yield frames", info

            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            backend = "OpenCV"
            if hasattr(cap, "getBackendName"):
                try:
                    backend = cap.getBackendName()
                except Exception:
                    backend = "OpenCV"

            cap.release()

            info.update({
                "width": w if w > 0 else frame.shape[1],
                "height": h if h > 0 else frame.shape[0],
                "fps": fps if fps > 0 else 30.0,
                "backend": backend,
            })
            return True, "Camera verified successfully", info

        except Exception as exc:
            return False, f"Error probing camera {camera_index}: {exc}", info

    def start(self) -> Tuple[bool, str]:
        """Initialize the camera hardware and start the asynchronous capture worker."""
        if self.is_active:
            logger.info("Camera is already active.")
            return True, "Camera already running"

        logger.info(
            "Initializing camera index %d (mock_mode=%s)...",
            self.camera_index,
            self.mock_mode,
        )

        try:
            if self.mock_mode:
                self._capture = MockCameraSource()
            else:
                self._capture = cv2.VideoCapture(self.camera_index)

            if not self._capture.isOpened():
                err_msg = f"Failed to open camera device at index {self.camera_index}"
                logger.warning(err_msg)
                self.release()
                return False, err_msg

            if self.mock_mode:
                self.backend = "SYNTHETIC"
            elif hasattr(self._capture, "getBackendName"):
                try:
                    self.backend = self._capture.getBackendName()
                except Exception:
                    self.backend = "OpenCV VideoCapture"
            else:
                self.backend = "OpenCV VideoCapture"

            # Verify initial frame readability
            ret, test_frame = self._capture.read()
            if not ret or test_frame is None or test_frame.size == 0:
                err_msg = f"Camera index {self.camera_index} opened but returned no valid frames"
                logger.warning(err_msg)
                self.release()
                return False, err_msg

            # Query dimensions and properties
            raw_w = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            raw_h = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            raw_fps = float(self._capture.get(cv2.CAP_PROP_FPS))

            self.width = raw_w if raw_w > 0 else test_frame.shape[1]
            self.height = raw_h if raw_h > 0 else test_frame.shape[0]
            self.hardware_fps = raw_fps if raw_fps > 0 else 30.0

            # Store the initial frame (converted to RGB)
            initial_rgb = cv2.cvtColor(test_frame, cv2.COLOR_BGR2RGB)
            if self.mirror:
                initial_rgb = cv2.flip(initial_rgb, 1)

            with self._lock:
                self._latest_frame = initial_rgb
                self._running = True

            # Start asynchronous background capture thread
            self._thread = threading.Thread(
                target=self._capture_worker,
                name="VisionCoreCameraWorker",
                daemon=True,
            )
            self._thread.start()

            logger.info(
                "Camera %d online: %dx%d @ %.1f FPS [Backend: %s]",
                self.camera_index,
                self.width,
                self.height,
                self.hardware_fps,
                self.backend,
            )
            return True, "Camera online"

        except Exception as exc:
            err_msg = f"Unexpected exception initializing camera {self.camera_index}: {exc}"
            logger.error(err_msg, exc_info=True)
            self.release()
            return False, err_msg

    def _capture_worker(self) -> None:
        """Asynchronous worker loop continuously polling the camera hardware."""
        consecutive_failures = 0
        max_consecutive_failures = 30
        last_calc_time = time.time()
        fps_frame_counter = 0

        logger.debug("Camera capture worker thread started")

        while self._running:
            if self._capture is None or not self._capture.isOpened():
                logger.warning("Camera hardware disconnected")
                break

            ret, frame = self._capture.read()
            now = time.time()

            if not ret or frame is None or frame.size == 0:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "Camera capture failed %d consecutive times; stopping capture worker",
                        consecutive_failures,
                    )
                    break
                time.sleep(0.01)
                continue

            consecutive_failures = 0

            # Color conversion to RGB for Pygame display
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if self.mirror:
                rgb_frame = cv2.flip(rgb_frame, 1)

            fps_frame_counter += 1
            if now - last_calc_time >= 1.0:
                self._actual_fps = fps_frame_counter / (now - last_calc_time)
                fps_frame_counter = 0
                last_calc_time = now

            with self._lock:
                self._latest_frame = rgb_frame

            # Yield briefly to maintain target FPS pacing without hogging CPU
            sleep_interval = max(0.001, (1.0 / self.target_fps) - (time.time() - now))
            time.sleep(sleep_interval)

        with self._lock:
            self._running = False
        logger.debug("Camera capture worker thread exited")

    def get_frame(self) -> Tuple[bool, Optional[np.ndarray], float]:
        """
        Thread-safely retrieve the latest captured RGB frame and timestamp.
        Returns: (success, frame_array, actual_fps)
        """
        with self._lock:
            if not self._running or self._latest_frame is None:
                return False, None, 0.0
            return True, self._latest_frame, self._actual_fps

    def stop(self) -> None:
        """Signal capture thread to terminate and wait cleanly."""
        logger.debug("Stopping camera capture...")
        with self._lock:
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
            self._thread = None

    def release(self) -> None:
        """Stop capture and release all camera hardware resources."""
        self.stop()
        with self._lock:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception as exc:
                    logger.warning("Error releasing camera device: %s", exc)
                self._capture = None
            self._latest_frame = None
            self._running = False
            self.width = 0
            self.height = 0
            self.hardware_fps = 0.0
        logger.info("Camera resources released successfully")
