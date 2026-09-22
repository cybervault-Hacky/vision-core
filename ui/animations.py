"""Reusable animation engine and interpolation utilities for VisionCore."""

from __future__ import annotations

import math
from typing import Callable, Tuple


class Easing:
    """Standard easing functions for natural sci-fi motion curves."""

    @staticmethod
    def linear(t: float) -> float:
        return max(0.0, min(1.0, t))

    @staticmethod
    def ease_in_quad(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * t

    @staticmethod
    def ease_out_quad(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * (2.0 - t)

    @staticmethod
    def ease_in_out_quad(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return 2.0 * t * t if t < 0.5 else -1.0 + (4.0 - 2.0 * t) * t

    @staticmethod
    def sine_wave(t: float, freq: float = 1.0) -> float:
        """Returns normalized sine wave in range [0.0, 1.0]."""
        return 0.5 + 0.5 * math.sin(t * 2.0 * math.pi * freq)


class FadeAnimation:
    """Smooth opacity transition helper."""

    def __init__(self, initial_alpha: float = 0.0, duration: float = 0.5):
        self.alpha = float(initial_alpha)
        self.target_alpha = float(initial_alpha)
        self.duration = max(0.001, duration)

    def fade_to(self, target: float, duration: float | None = None) -> None:
        self.target_alpha = max(0.0, min(1.0, target))
        if duration is not None:
            self.duration = max(0.001, duration)

    def update(self, dt: float) -> float:
        if math.isclose(self.alpha, self.target_alpha, abs_tol=1e-4):
            self.alpha = self.target_alpha
            return self.alpha

        step = dt / self.duration
        if self.alpha < self.target_alpha:
            self.alpha = min(self.target_alpha, self.alpha + step)
        else:
            self.alpha = max(self.target_alpha, self.alpha - step)
        return self.alpha


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
    """Controlled progress interpolator for boot sequence and initialization steps."""

    def __init__(self, duration: float = 2.0, easing_fn: Callable[[float], float] = Easing.ease_in_out_quad):
        self.duration = max(0.001, duration)
        self.easing_fn = easing_fn
        self.elapsed = 0.0
        self.progress = 0.0
        self.is_complete = False

    def reset(self, new_duration: float | None = None) -> None:
        if new_duration is not None:
            self.duration = max(0.001, new_duration)
        self.elapsed = 0.0
        self.progress = 0.0
        self.is_complete = False

    def update(self, dt: float) -> float:
        if self.is_complete:
            return 1.0

        self.elapsed += dt
        norm = max(0.0, min(1.0, self.elapsed / self.duration))
        self.progress = self.easing_fn(norm)

        if norm >= 1.0:
            self.is_complete = True
            self.progress = 1.0

        return self.progress


def lerp_color(
    color_a: Tuple[int, int, int],
    color_b: Tuple[int, int, int],
    t: float,
) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colors."""
    t = max(0.0, min(1.0, t))
    return (
        int(color_a[0] + (color_b[0] - color_a[0]) * t),
        int(color_a[1] + (color_b[1] - color_a[1]) * t),
        int(color_a[2] + (color_b[2] - color_a[2]) * t),
    )
