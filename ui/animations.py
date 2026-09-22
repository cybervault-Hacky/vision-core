"""Reusable animation engine and interpolation utilities for VisionCore."""

from __future__ import annotations

import math


def ease_in_out_quad(t: float) -> float:
    """Quadratic ease-in/ease-out curve normalized to [0.0, 1.0]."""
    t = max(0.0, min(1.0, t))
    return 2.0 * t * t if t < 0.5 else -1.0 + (4.0 - 2.0 * t) * t


class RotationAnimation:
    """Continuous angular rotation for reticles, compasses, and scanners."""

    def __init__(self, speed_deg_per_sec: float = 30.0, initial_angle: float = 0.0):
        self.angle = float(initial_angle)
        self.speed = speed_deg_per_sec

    def update(self, dt: float) -> float:
        self.angle = (self.angle + self.speed * dt) % 360.0
        return self.angle


class PulseAnimation:
    """Smooth harmonic pulse oscillator for glowing borders and live indicators."""

    def __init__(
        self,
        min_val: float = 0.2,
        max_val: float = 1.0,
        frequency_hz: float = 1.0,
    ):
        self.min_val = min_val
        self.max_val = max_val
        self.frequency = frequency_hz
        self._elapsed = 0.0
        self.value = min_val

    def update(self, dt: float) -> float:
        self._elapsed += dt
        wave = 0.5 + 0.5 * math.sin(self._elapsed * 2.0 * math.pi * self.frequency)
        self.value = self.min_val + wave * (self.max_val - self.min_val)
        return self.value


class ScanlineAnimation:
    """Vertical scanning beam moving smoothly down the camera viewport."""

    def __init__(self, speed: float = 0.4, initial_pos: float = 0.0):
        self.position = initial_pos  # 0.0 (top) to 1.0 (bottom)
        self.speed = speed

    def update(self, dt: float) -> float:
        self.position = (self.position + self.speed * dt) % 1.0
        return self.position


class ProgressAnimation:
    """Controlled progress interpolator for the boot sequence and initialization steps."""

    def __init__(self, duration: float = 2.0):
        self.duration = max(0.001, duration)
        self.elapsed = 0.0
        self.progress = 0.0
        self.is_complete = False

    def update(self, dt: float) -> float:
        if self.is_complete:
            return 1.0

        self.elapsed += dt
        norm = max(0.0, min(1.0, self.elapsed / self.duration))
        self.progress = ease_in_out_quad(norm)

        if norm >= 1.0:
            self.is_complete = True
            self.progress = 1.0

        return self.progress
