"""Real-time gesture recognition engine for VisionCore.

Pipeline::

    HandTrackingResult
            |
            v
    landmark features          (features.extract)
            |
            v
    pose classification        (classifier.classify / classifier.select)
            |
            v
    temporal detection         (temporal.SwipeDetector)
            |
            v
    stabilisation + debounce   (this module)
            |
            v
    GestureResult

The engine performs no image processing and runs no additional model: it works
exclusively on the landmarks the tracking layer already produced, so its cost is
a few hundred floating point operations per hand and per frame.

All timing uses a monotonic clock (``time.perf_counter``); timestamps in the
emitted results are monotonic seconds, suitable for measuring intervals.

No operating system action is performed anywhere in this module. Recognising a
gesture only produces data.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

from app.gestures.classifier import PoseCandidate, classify, select
from app.gestures.features import HandFeatures, extract
from app.gestures.temporal import MotionHistory, SwipeDetector
from app.gestures.types import (
    EMPTY_EVIDENCE,
    Gesture,
    GesturePhase,
    GestureResult,
    GestureSettings,
    GestureSnapshot,
    GestureState,
    Point,
)
from app.hand_tracking import LANDMARK_NAMES, Hand, HandTrackingResult

logger = logging.getLogger("visioncore.gestures")

# The hand landmark model always emits the full landmark set; anything fewer is
# unusable, so an incomplete detection is skipped instead of guessed at.
REQUIRED_LANDMARKS = len(LANDMARK_NAMES)


@dataclass
class _HandSession:
    """Recognition state for one tracked hand, isolated from every other hand."""

    key: str
    stable: Gesture = Gesture.NONE
    confidence: float = 0.0
    pending: Gesture = Gesture.NONE
    pending_frames: int = 0
    absent_frames: int = 0
    released: Gesture = Gesture.NONE
    changed: bool = False
    last_seen: float = 0.0
    last_center: Optional[Point] = None
    evidence: Mapping[str, object] = field(default_factory=lambda: EMPTY_EVIDENCE)
    handedness: Optional[str] = None
    history: MotionHistory = field(default_factory=MotionHistory)
    detector: SwipeDetector = field(default_factory=SwipeDetector)
    swipe: Gesture = Gesture.NONE
    swipe_expiry: float = 0.0
    swipe_fired: bool = False
    swipe_evidence: Mapping[str, object] = field(default_factory=lambda: EMPTY_EVIDENCE)

    def reset_recognition(self) -> None:
        """Drop pose and motion history (used when a hand jumps or is lost)."""
        self.stable = Gesture.NONE
        self.pending = Gesture.NONE
        self.pending_frames = 0
        self.confidence = 0.0
        self.evidence = EMPTY_EVIDENCE
        self.history.clear()
        self.detector.reset()
        self.swipe = Gesture.NONE
        self.swipe_expiry = 0.0
        self.swipe_fired = False
        self.swipe_evidence = EMPTY_EVIDENCE


class GestureEngine:
    """Stateful recogniser exposing a frame-by-frame API over hand landmarks."""

    def __init__(self, settings: Optional[GestureSettings] = None) -> None:
        self.settings = (settings or GestureSettings()).clamped()
        self._sessions: Dict[str, _HandSession] = {}
        self._primary_key: Optional[str] = None
        self._events = 0
        self._latency_ms = 0.0
        self._state = (
            GestureState.SEARCHING if self.settings.enabled else GestureState.DISABLED
        )

    # -- lifecycle --------------------------------------------------------- #

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def state(self) -> GestureState:
        return self._state

    @property
    def events(self) -> int:
        """Number of gesture transitions (starts and releases) observed."""
        return self._events

    @property
    def latency_ms(self) -> float:
        """Duration of the last recognition pass, measured."""
        return self._latency_ms

    @property
    def sessions(self) -> Tuple[str, ...]:
        """Identities of the hands currently held in recognition state."""
        return tuple(self._sessions)

    def reset(self) -> None:
        """Forget every hand session (camera loss, retry, shutdown)."""
        self._sessions.clear()
        self._primary_key = None
        self._state = (
            GestureState.SEARCHING if self.settings.enabled else GestureState.DISABLED
        )

    def close(self) -> None:
        """Release recognition state. The engine owns no native resources."""
        self.reset()

    def disabled_snapshot(self) -> GestureSnapshot:
        """Snapshot used when gesture recognition is switched off."""
        return GestureSnapshot(
            state=GestureState.DISABLED,
            result=GestureResult(),
            hands=(),
            latency_ms=0.0,
        )

    # -- recognition ------------------------------------------------------- #

    def process(
        self,
        tracking: HandTrackingResult,
        aspect: float = 1.0,
        now: Optional[float] = None,
    ) -> GestureSnapshot:
        """Recognise gestures for every hand present in a tracking result."""
        if not self.settings.enabled:
            return self.disabled_snapshot()

        started = time.perf_counter()
        timestamp = now if now is not None else started
        hands = tracking.hands if tracking.detected else ()

        if not hands:
            result, state = self._advance_absent_sessions(timestamp)
            self._latency_ms = (time.perf_counter() - started) * 1000.0
            self._state = state
            return GestureSnapshot(
                state=state,
                result=result,
                hands=(),
                latency_ms=self._latency_ms,
            )

        results = []
        primary: Optional[GestureResult] = None
        primary_rank = -1.0
        for index, hand in enumerate(hands):
            if len(hand.landmarks) < REQUIRED_LANDMARKS:
                continue
            features = extract(hand.landmarks, aspect)
            session = self._session_for(hand, index, features.palm_center, timestamp)
            result = self._evaluate(session, hand, features, timestamp)
            results.append(result)
            rank = hand.confidence if hand.confidence is not None else 0.0
            if rank > primary_rank:
                primary_rank = rank
                primary = result
                self._primary_key = session.key

        self._prune_sessions(timestamp)

        if primary is None:
            primary = GestureResult(timestamp=timestamp)
            state = GestureState.ANALYZING
        elif any(result.recognized for result in results):
            state = GestureState.RECOGNIZED
        else:
            state = GestureState.ANALYZING

        self._latency_ms = (time.perf_counter() - started) * 1000.0
        self._state = state
        return GestureSnapshot(
            state=state,
            result=primary,
            hands=tuple(results),
            latency_ms=self._latency_ms,
        )

    # -- session management ------------------------------------------------ #

    def _session_for(
        self,
        hand: Hand,
        index: int,
        center: Point,
        now: float,
    ) -> _HandSession:
        """Return the session that owns this detection.

        Hands are identified by the tracking engine's handedness label, so
        landmarks from different hands are never mixed. When handedness is
        unavailable (the model occasionally omits it) a detection is bound to its
        slot index instead. A hand that jumps far enough to be a different hand
        restarts its own recognition state.
        """
        key = hand.handedness or f"SLOT{index}"
        session = self._sessions.get(key)
        if session is None:
            if len(self._sessions) >= self.settings.max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.last_seen)
                del self._sessions[oldest.key]
            session = _HandSession(
                key=key,
                history=MotionHistory(self.settings.swipe_window_sec),
                detector=SwipeDetector(self.settings),
            )
            self._sessions[key] = session

        if session.last_center is not None:
            jump = math.hypot(
                center[0] - session.last_center[0],
                center[1] - session.last_center[1],
            )
            if jump > self.settings.hand_jump_threshold:
                session.reset_recognition()

        session.key = key
        session.handedness = hand.handedness
        session.last_center = center
        session.last_seen = now
        session.absent_frames = 0
        return session

    def _prune_sessions(self, now: float) -> None:
        """Drop sessions whose hand has not been seen for a while."""
        stale = [
            key
            for key, session in self._sessions.items()
            if now - session.last_seen > self.settings.session_timeout_sec
        ]
        for key in stale:
            del self._sessions[key]
            if key == self._primary_key:
                self._primary_key = None

    # -- per hand evaluation ----------------------------------------------- #

    def _evaluate(
        self,
        session: _HandSession,
        hand: Hand,
        features: HandFeatures,
        now: float,
    ) -> GestureResult:
        """Run one recognition pass for a single hand."""
        session.changed = False
        session.released = Gesture.NONE

        self._track_motion(session, features, now)
        candidates = classify(features, self.settings, session.stable)
        self._stabilise(session, select(candidates, self.settings))

        gesture = session.stable
        active = gesture is not Gesture.NONE
        confidence = session.confidence
        evidence = session.evidence
        released = session.released
        phase = GesturePhase.ACTIVE if active else GesturePhase.NONE

        if session.changed:
            if active:
                phase = GesturePhase.START
            else:
                # Release frame: surface the gesture that just ended, once.
                phase = GesturePhase.RELEASE
                gesture = released
                session.confidence = 0.0
                session.evidence = EMPTY_EVIDENCE
        elif not active:
            confidence = 0.0
            evidence = EMPTY_EVIDENCE

        # A recognised swipe overrides the held pose for a short hold window. It
        # is an event, so it never emits a release for the static gesture
        # underneath, which stays tracked and resumes when the swipe ends.
        if session.swipe is not Gesture.NONE:
            if session.swipe_fired or now < session.swipe_expiry:
                gesture = session.swipe
                active = True
                confidence = self._swipe_confidence(session)
                evidence = session.swipe_evidence
                phase = GesturePhase.START if session.swipe_fired else GesturePhase.ACTIVE
                session.swipe_fired = False
            else:
                gesture = session.swipe
                phase = GesturePhase.RELEASE
                active = False
                confidence = self._swipe_confidence(session)
                released = session.swipe
                session.changed = True
                session.swipe = Gesture.NONE
                session.swipe_expiry = 0.0
                session.swipe_evidence = EMPTY_EVIDENCE

        if session.changed:
            self._events += 1

        return GestureResult(
            gesture=gesture,
            phase=phase,
            confidence=confidence,
            active=active,
            changed=session.changed,
            released=released,
            handedness=hand.handedness,
            timestamp=now,
            evidence=evidence,
        )

    def _track_motion(self, session: _HandSession, features: HandFeatures, now: float) -> None:
        """Feed the motion buffer and fire a swipe when one is recognised.

        Swipes are only looked for while the held pose is not a pinch: pinch is
        the highest priority pose and is already reserved for click/drag or mute
        control, so it must not double as a swipe.
        """
        if session.stable is Gesture.PINCH:
            session.history.clear()
            return

        session.history.append(now, features.palm_center)
        event = session.detector.evaluate(session.history, now)
        if event is None:
            return

        session.swipe = event.gesture
        session.swipe_expiry = now + self.settings.swipe_hold_sec
        session.swipe_fired = True
        session.changed = True
        # The motion buffer is deliberately left intact: the detector needs to
        # see the hand settle before it will arm another swipe.
        session.swipe_evidence = {
            "swipe_displacement": round(event.displacement, 4),
            "swipe_velocity": round(event.velocity, 3),
            "swipe_duration": round(event.duration, 3),
            "swipe_consistency": round(event.consistency, 3),
        }
        logger.debug(
            "Swipe recognised: %s (dx=%.3f v=%.2f/s in %.3fs)",
            event.gesture.value,
            event.displacement,
            event.velocity,
            event.duration,
        )

    def _stabilise(self, session: _HandSession, candidate: Optional[PoseCandidate]) -> None:
        """Require consecutive agreeing frames before a pose becomes stable."""
        settings = self.settings
        gesture = candidate.gesture if candidate is not None else Gesture.NONE
        score = candidate.score if candidate is not None else 0.0

        if gesture is session.stable:
            session.pending = Gesture.NONE
            session.pending_frames = 0
            if gesture is not Gesture.NONE:
                # Live evidence keeps the reported confidence honest.
                session.confidence = score
                session.evidence = (
                    candidate.evidence if candidate is not None else EMPTY_EVIDENCE
                )
            return

        if gesture is session.pending:
            session.pending_frames += 1
        else:
            session.pending = gesture
            session.pending_frames = 1

        required = (
            settings.release_frames if gesture is Gesture.NONE else settings.stability_frames
        )
        if session.pending_frames < required:
            return

        previous = session.stable
        session.stable = gesture
        session.pending = Gesture.NONE
        session.pending_frames = 0
        session.changed = True
        session.released = previous

        if gesture is not Gesture.NONE:
            session.confidence = score
            session.evidence = (
                candidate.evidence if candidate is not None else EMPTY_EVIDENCE
            )
            logger.debug("Gesture recognised: %s (%.2f)", gesture.value, score)

    @staticmethod
    def _swipe_confidence(session: _HandSession) -> float:
        """Confidence for a swipe, derived from its measured displacement."""
        displacement = session.swipe_evidence.get("swipe_displacement")
        if not isinstance(displacement, (int, float)):
            return 0.0
        # Saturated frame-fraction of the travel actually measured.
        return min(1.0, float(displacement) / 0.45)

    def _advance_absent_sessions(self, now: float) -> Tuple[GestureResult, GestureState]:
        """Handle frames with no tracked hand: release gestures gracefully."""
        primary = self._sessions.get(self._primary_key) if self._primary_key else None
        if primary is None and self._sessions:
            primary = max(self._sessions.values(), key=lambda s: s.last_seen)

        for session in list(self._sessions.values()):
            session.absent_frames += 1
            session.changed = False
            session.released = Gesture.NONE
            session.history.clear()
            session.pending = Gesture.NONE
            session.pending_frames = 0

            if session.swipe is not Gesture.NONE:
                swipe = session.swipe
                session.swipe = Gesture.NONE
                session.swipe_fired = False
                session.swipe_expiry = 0.0
                if session is primary:
                    self._events += 1
                    return (
                        GestureResult(
                            gesture=swipe,
                            phase=GesturePhase.RELEASE,
                            active=False,
                            changed=True,
                            released=swipe,
                            handedness=session.handedness,
                            timestamp=now,
                        ),
                        GestureState.SEARCHING,
                    )

            if (
                session.stable is not Gesture.NONE
                and session.absent_frames >= self.settings.release_frames
            ):
                released = session.stable
                session.stable = Gesture.NONE
                session.confidence = 0.0
                session.evidence = EMPTY_EVIDENCE
                session.detector.reset()
                if session is primary:
                    self._events += 1
                    return (
                        GestureResult(
                            gesture=released,
                            phase=GesturePhase.RELEASE,
                            active=False,
                            changed=True,
                            released=released,
                            handedness=session.handedness,
                            timestamp=now,
                        ),
                        GestureState.SEARCHING,
                    )

        self._prune_sessions(now)
        return GestureResult(timestamp=now), GestureState.SEARCHING
