"""Temporal gesture detection: horizontal swipes from palm movement.

Static landmarks say nothing about movement, so swipes are recognised from a
short, bounded history of palm centre positions. The buffer only stores the
minimum required data (timestamp and one point per frame), never grows without
limit, and is cleared whenever tracking is lost.

A swipe must satisfy all of the following, which together reject hand jitter,
tremor and ordinary repositioning:

* a valid history spanning a plausible time window,
* net horizontal travel above ``swipe_distance_threshold``,
* horizontal dominance over vertical travel,
* peak speed above ``swipe_velocity_threshold``,
* direction consistency (the motion must not reverse mid-window),
* a cooldown window after every recognised swipe.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from app.gestures.types import Gesture, GestureSettings

Point = Tuple[float, float]


@dataclass(frozen=True, slots=True)
class MotionSample:
    """One palm centre observation."""

    timestamp: float
    point: Point


@dataclass(frozen=True, slots=True)
class SwipeEvent:
    """A recognised swipe with the evidence that produced it."""

    gesture: Gesture
    displacement: float
    velocity: float
    duration: float
    consistency: float


class MotionHistory:
    """Bounded ring buffer of palm centre samples."""

    def __init__(self, window_sec: float = 0.40, max_samples: int = 48) -> None:
        self.window_sec = window_sec
        self._samples: Deque[MotionSample] = deque(maxlen=max_samples)

    def append(self, timestamp: float, point: Point) -> None:
        self._samples.append(MotionSample(timestamp, point))
        self.prune(timestamp)

    def prune(self, now: float) -> None:
        """Drop samples that fell out of the detection window."""
        cutoff = now - self.window_sec
        samples = self._samples
        while samples and samples[0].timestamp < cutoff:
            samples.popleft()

    def clear(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def samples(self) -> Tuple[MotionSample, ...]:
        return tuple(self._samples)


class SwipeDetector:
    """Recognises left/right swipes and enforces the cooldown between them."""

    def __init__(self, settings: Optional[GestureSettings] = None) -> None:
        self.settings = settings or GestureSettings()
        self._cooldown_until = 0.0
        self._last_event: Optional[SwipeEvent] = None
        self._cooling = False
        self._armed = True

    @property
    def is_cooling(self) -> bool:
        """True while the detector is still blocking repeat triggers."""
        return self._cooling

    @property
    def last_event(self) -> Optional[SwipeEvent]:
        return self._last_event

    def reset(self) -> None:
        self._cooldown_until = 0.0
        self._last_event = None
        self._cooling = False
        self._armed = True

    def _has_settled(self, samples: Tuple[MotionSample, ...]) -> bool:
        """True once the hand has slowed down enough to arm a new swipe.

        This is what stops one continuous sweep from firing again and again as
        soon as the cooldown expires: the hand must come to rest (or nearly so)
        before another swipe can be recognised.
        """
        recent = samples[-3:]
        if len(recent) < 2:
            return True
        travel = abs(recent[-1].point[0] - recent[0].point[0]) + abs(
            recent[-1].point[1] - recent[0].point[1]
        )
        return travel < 0.4 * self.settings.swipe_distance_threshold

    def evaluate(self, history: MotionHistory, now: float) -> Optional[SwipeEvent]:
        """Return a swipe when the buffered motion satisfies every criterion."""
        self._cooling = now < self._cooldown_until
        samples = history.samples

        if self._cooling:
            return None

        if not self._armed:
            if self._has_settled(samples):
                # Start a clean window once the hand has settled, so the next
                # swipe is measured from fresh motion only.
                self._armed = True
                history.clear()
            return None

        if len(samples) < self.settings.swipe_min_samples:
            return None
        first, last = samples[0], samples[-1]
        duration = last.timestamp - first.timestamp
        if duration < self.settings.swipe_min_duration_sec:
            return None

        dx = last.point[0] - first.point[0]
        dy = last.point[1] - first.point[1]
        if abs(dx) < self.settings.swipe_distance_threshold:
            return None

        # Horizontal dominance: the travel must be mostly sideways, otherwise a
        # vertical wave or a diagonal repositioning would read as a swipe.
        travelled_2d = abs(dx) + abs(dy)
        if travelled_2d <= 1e-6 or abs(dx) / travelled_2d < self.settings.swipe_axis_ratio:
            return None

        # Direction consistency: how much of the travelled horizontal path
        # actually moved in the net direction. A hand that wobbles back and
        # forth scores low and is rejected.
        travelled = 0.0
        previous = first.point[0]
        for sample in samples[1:]:
            travelled += abs(sample.point[0] - previous)
            previous = sample.point[0]
        consistency = abs(dx) / travelled if travelled > 1e-6 else 0.0
        if consistency < self.settings.swipe_consistency_threshold:
            return None

        velocity = abs(dx) / duration
        if velocity < self.settings.swipe_velocity_threshold:
            return None

        event = SwipeEvent(
            gesture=Gesture.SWIPE_RIGHT if dx > 0.0 else Gesture.SWIPE_LEFT,
            displacement=abs(dx),
            velocity=velocity,
            duration=duration,
            consistency=consistency,
        )
        self._last_event = event
        self._cooldown_until = now + self.settings.swipe_cooldown
        self._armed = False
        return event
