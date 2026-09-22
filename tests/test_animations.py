"""Unit tests for animation engine and tweening primitives."""

from __future__ import annotations

import pytest

from ui.animations import (
    Easing,
    FadeAnimation,
    ProgressAnimation,
    PulseAnimation,
    RotationAnimation,
    ScanlineAnimation,
    lerp_color,
)


class TestEasing:
    """Validate mathematical properties of sci-fi motion curves."""

    def test_easing_boundaries(self):
        for fn in (Easing.linear, Easing.ease_in_quad, Easing.ease_out_quad, Easing.ease_in_out_quad):
            assert fn(0.0) == 0.0
            assert fn(1.0) == 1.0

    def test_sine_wave_range(self):
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            val = Easing.sine_wave(t)
            assert 0.0 <= val <= 1.0


class TestFadeAnimation:
    """Validate opacity interpolator."""

    def test_fade_in_progression(self):
        fade = FadeAnimation(initial_alpha=0.0, duration=1.0)
        assert fade.alpha == 0.0

        fade.fade_to(1.0)
        fade.update(0.5)
        assert 0.49 <= fade.alpha <= 0.51

        fade.update(0.6)
        assert fade.alpha == 1.0

    def test_fade_out_progression(self):
        fade = FadeAnimation(initial_alpha=1.0, duration=1.0)
        fade.fade_to(0.0)
        fade.update(0.5)
        assert 0.49 <= fade.alpha <= 0.51
        fade.update(0.6)
        assert fade.alpha == 0.0


class TestRotationAnimation:
    """Validate angular rotation and wrap-around."""

    def test_rotation_progress(self):
        rot = RotationAnimation(speed_deg_per_sec=90.0, initial_angle=0.0)
        rot.update(1.0)
        assert rot.angle == 90.0
        rot.update(3.0)  # Total 360 deg -> wraps to 0.0
        assert rot.angle == 0.0


class TestPulseAnimation:
    """Validate harmonic pulse oscillator."""

    def test_pulse_bounds(self):
        pulse = PulseAnimation(min_val=0.2, max_val=0.8, frequency_hz=2.0)
        for i in range(20):
            val = pulse.update(0.05)
            assert 0.199 <= val <= 0.801


class TestScanlineAnimation:
    """Validate vertical sweeping laser motion."""

    def test_scanline_sweep_and_wrap(self):
        scan = ScanlineAnimation(speed=0.5, initial_pos=0.0)
        scan.update(1.0)
        assert scan.position == 0.5
        scan.update(1.2)  # wraps around 1.0
        assert 0.0 <= scan.position < 1.0


class TestProgressAnimation:
    """Validate boot progress tracker."""

    def test_progress_completion(self):
        prog = ProgressAnimation(duration=1.0)
        assert prog.is_complete is False

        prog.update(0.5)
        assert 0.0 < prog.progress < 1.0
        assert prog.is_complete is False

        prog.update(0.6)
        assert prog.progress == 1.0
        assert prog.is_complete is True


def test_lerp_color():
    black = (0, 0, 0)
    white = (255, 255, 255)
    mid = lerp_color(black, white, 0.5)
    assert mid == (127, 127, 127)
