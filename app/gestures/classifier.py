"""Static hand pose classification from landmark geometry.

Every pose is scored continuously in ``0..1`` from geometric evidence instead of
being decided by boolean rules. The score is used both to accept or reject a
gesture (via the configured confidence threshold) and to report a genuine
confidence to the HUD, and it keeps a pose that only *nearly* matches from being
forced into a gesture.

Threshold values embedded in the ramps below were calibrated against landmark
sets produced by the bundled MediaPipe hand model on real photographs of each
pose, then kept deliberately conservative: an ambiguous hand reports ``NONE``
rather than a wrong gesture.

Poses that are geometrically exclusive by construction (an open palm cannot be a
fist) are still resolved through an explicit priority order so the result is
always deterministic when two candidates overlap - a pointing hand, for example,
produces evidence for both ``POINT`` and ``FIST``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Tuple

from app.gestures.features import HandFeatures, ramp
from app.gestures.types import Gesture, GestureSettings

# Deterministic resolution order for the static pose set. Motion gestures are
# handled by the temporal detector and always take precedence over these.
POSE_PRIORITY: Tuple[Gesture, ...] = (
    Gesture.PINCH,
    Gesture.POINT,
    Gesture.TWO_FINGER,
    Gesture.FIST,
    Gesture.OPEN_PALM,
)

# Evidence ramps (geometric constants, not user settings).
_DOMINANT_LO, _DOMINANT_HI = 0.62, 0.90        # one or two leading fingers
_FOLDED_LO, _FOLDED_HI = 0.55, 0.20            # trailing fingers must be curled
_FIST_LO, _FIST_HI = 0.45, 0.15                # every finger must be curled
_PALM_THIRD_LO, _PALM_THIRD_HI = 0.28, 0.50    # third most extended finger
_PALM_SUM_LO, _PALM_SUM_HI = 2.00, 2.60        # combined extension of four fingers
_PINCH_CONTACT_SPAN = 0.32                     # pinch distance ramp width
_PINCH_LIFT_SPAN = 0.20                        # pinch lift ramp width
_PINCH_LIFT_WEIGHT = 0.75


@dataclass(frozen=True, slots=True)
class PoseCandidate:
    """One scored pose hypothesis for a hand."""

    gesture: Gesture
    score: float
    evidence: Mapping[str, object]


def _folded(extension: float) -> float:
    """Score a finger for being folded: 1 when clearly curled, 0 when extended."""
    return ramp(_FOLDED_LO - extension, 0.0, _FOLDED_LO - _FOLDED_HI)


def _pinch_score(features: HandFeatures, settings: GestureSettings, held: bool) -> float:
    """Score a thumb-index pinch, normalised by palm scale and gated on lift.

    ``held`` relaxes the distance gate while a pinch is already established so a
    hand that eases its fingers apart releases cleanly instead of flickering.
    """
    ratio = features.pinch_ratio
    distance_gate = settings.pinch_release_threshold if held else settings.pinch_threshold
    if ratio >= distance_gate or features.pinch_lift < settings.pinch_lift_threshold:
        return 0.0

    contact = ramp(distance_gate - ratio, 0.0, _PINCH_CONTACT_SPAN)
    lift = ramp(
        features.pinch_lift,
        settings.pinch_lift_threshold,
        settings.pinch_lift_threshold + _PINCH_LIFT_SPAN,
    )
    return _PINCH_LIFT_WEIGHT * lift + (1.0 - _PINCH_LIFT_WEIGHT) * contact


def classify(
    features: HandFeatures,
    settings: GestureSettings,
    held_gesture: Gesture = Gesture.NONE,
) -> Tuple[PoseCandidate, ...]:
    """Score every static pose for a hand.

    Returns candidates in :data:`POSE_PRIORITY` order. Scores are geometric
    evidence, so callers can both threshold them and display them as a real
    confidence.
    """
    index, middle, ring, pinky = features.extensions

    pinch = _pinch_score(features, settings, held_gesture is Gesture.PINCH)
    # POINT and TWO_FINGER are separated by the middle finger alone, so that
    # finger is decisive while the ring and pinky (the two hardest to track and
    # the two most often partly curled) are averaged for tolerance.
    point = (
        ramp(index, _DOMINANT_LO, _DOMINANT_HI)
        * _folded(middle)
        * ((_folded(ring) + _folded(pinky)) / 2.0)
    )
    two_finger = ramp(min(index, middle), _DOMINANT_LO, _DOMINANT_HI) * (
        (_folded(ring) + _folded(pinky)) / 2.0
    )
    fist = sum(ramp(_FIST_LO - e, 0.0, _FIST_LO - _FIST_HI) for e in features.extensions) / 4.0
    open_palm = ramp(features.third_extension, _PALM_THIRD_LO, _PALM_THIRD_HI) * ramp(
        features.extension_sum, _PALM_SUM_LO, _PALM_SUM_HI
    )

    scores = {
        Gesture.PINCH: pinch,
        Gesture.POINT: point,
        Gesture.TWO_FINGER: two_finger,
        Gesture.FIST: fist,
        Gesture.OPEN_PALM: open_palm,
    }

    return tuple(
        PoseCandidate(
            gesture=gesture,
            score=min(1.0, max(0.0, scores[gesture])),
            evidence=MappingProxyType(
                {
                    "extensions": features.extensions,
                    "thumb_extension": round(features.thumb_extension, 4),
                    "pinch_ratio": round(features.pinch_ratio, 4),
                    "pinch_lift": round(features.pinch_lift, 4),
                    "scores": {g.value: round(s, 4) for g, s in scores.items()},
                }
            ),
        )
        for gesture in POSE_PRIORITY
    )


def select(
    candidates: Tuple[PoseCandidate, ...],
    settings: GestureSettings,
) -> PoseCandidate | None:
    """Pick the highest priority pose that clears the confidence threshold."""
    for candidate in candidates:
        if candidate.score >= settings.confidence_threshold:
            return candidate
    return None
