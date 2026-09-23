"""Cursor mapping and smoothing for touchless mouse control.

The mapper turns a normalised index fingertip position (camera space, already
mirrored by the tracking layer) into a desktop pixel position:

    fingertip (0..1)  ->  control region  ->  normalised desktop  ->  pixels

Nothing here is resolution dependent: the desktop geometry is read from the
platform backend at runtime and the control region is expressed as a fraction of
the camera frame, so the cursor uses the same proportional area on any screen,
any video resolution and any camera without calibration.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from app.controls.backend import ScreenGeometry
from app.controls.safety import ControlSettings

Point = Tuple[float, float]


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


class CursorMapper:
    """Maps fingertip positions to the desktop and smooths the motion."""

    def __init__(self, settings: ControlSettings, geometry: ScreenGeometry) -> None:
        self.settings = settings
        self.geometry = geometry
        self._position: Optional[Point] = None   # smoothed normalised desktop
        self._commanded: Optional[Point] = None  # last position sent to the OS

    # -- geometry ---------------------------------------------------------- #

    def set_geometry(self, geometry: ScreenGeometry) -> None:
        """Adopt a new desktop geometry (screen changes, resolution change)."""
        if geometry != self.geometry:
            self.geometry = geometry
            self.reset()

    def set_settings(self, settings: ControlSettings) -> None:
        self.settings = settings

    def reset(self) -> None:
        """Forget the pointer position; the next update starts fresh."""
        self._position = None
        self._commanded = None

    @property
    def position(self) -> Optional[Point]:
        """Current smoothed pointer position in normalised desktop space."""
        return self._position

    @property
    def region(self) -> Tuple[float, float, float, float]:
        """The active control region in camera space as ``(x0, y0, x1, y1)``."""
        margin = self.settings.control_region_margin
        return (margin, margin, 1.0 - margin, 1.0 - margin)

    # -- mapping ----------------------------------------------------------- #

    def map_to_desktop(self, fingertip: Point) -> Point:
        """Map a fingertip position onto the normalised desktop (0..1)."""
        x0, y0, x1, y1 = self.region
        span_x = max(1e-6, x1 - x0)
        span_y = max(1e-6, y1 - y0)
        # The frame is mirrored by the tracking layer, so the horizontal axis is
        # used directly: a hand moving right on screen moves the cursor right.
        nx = _clamp01((fingertip[0] - x0) / span_x)
        ny = _clamp01((fingertip[1] - y0) / span_y)
        return (nx, ny)

    def to_pixels(self, position: Point) -> Tuple[int, int]:
        """Convert a normalised desktop position into screen pixels."""
        geometry = self.geometry
        x = geometry.x + position[0] * max(1, geometry.width - 1)
        y = geometry.y + position[1] * max(1, geometry.height - 1)
        return (int(round(x)), int(round(y)))

    # -- smoothing --------------------------------------------------------- #

    def update(self, dt: float, fingertip: Point) -> Optional[Tuple[int, int]]:
        """Advance the pointer and return the pixel position to send to the OS.

        Returns ``None`` when the movement is below the configured deadzone or
        the pixel target has not changed, so the operating system is only called
        when the cursor actually needs to move.
        """
        target = self.map_to_desktop(fingertip)
        if self._position is None:
            # First sample after engaging: start where the finger is.
            self._position = target
        else:
            # Exponential smoothing with a time constant, so the feel does not
            # change with the frame rate. Smoothing is a separate layer from the
            # landmark smoothing on purpose.
            tau = 0.005 + self.settings.cursor_smoothing * 0.25
            alpha = 1.0 - math.exp(-max(1e-4, dt) / tau)
            gain = alpha * self.settings.cursor_speed
            gain = 0.02 if gain < 0.02 else (1.0 if gain > 1.0 else gain)
            self._position = (
                self._position[0] + (target[0] - self._position[0]) * gain,
                self._position[1] + (target[1] - self._position[1]) * gain,
            )

        position = self._position
        if position is None:
            return None

        if self._commanded is not None:
            distance = math.hypot(position[0] - self._commanded[0], position[1] - self._commanded[1])
            if distance < self.settings.cursor_deadzone:
                return None

        pixel = self.to_pixels(position)
        if self._commanded is not None:
            previous = self.to_pixels(self._commanded)
            movement = math.hypot(pixel[0] - previous[0], pixel[1] - previous[1])
            if movement < self.settings.min_movement_px:
                return None

        self._commanded = position
        return pixel

    def mark_commanded(self, pixel: Tuple[int, int]) -> None:
        """Record that a pixel position was successfully delivered to the OS."""
        if self._position is not None:
            self._commanded = self._position

    def current_pixel(self) -> Optional[Tuple[int, int]]:
        if self._position is None:
            return None
        return self.to_pixels(self._position)
