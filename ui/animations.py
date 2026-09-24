"""Animation primitives for VisionCore.

Only what the redesigned interface actually uses: a harmonic pulse for live
indicators and a controlled progress interpolator for the boot and shutdown
sequences. Everything here is cheap by construction - no per-frame allocation,
no surfaces, no particles.
"""

from __future__ import annotations

import math


def ease_in_out_quad(t: float) -> float:
    """Quadratic ease-in/ease-out curve normalized to [0.0, 1.0]."""
    t = max(0.0, min(1.0, t))
    return 2.0 * t * t if t < 0.5 else -1.0 + (4.0 - 2.0 * t) * t


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
