"""Landmark feature extraction for VisionCore gesture recognition.

Finger poses are derived purely from landmark geometry - never from image
templates, camera regions or absolute screen coordinates - so the same gesture is
recognised at any position, rotation, scale, distance and handedness.

Geometry notes
--------------
* Landmarks arrive normalised to the frame, which is anisotropic (x is divided by
  the frame width, y by its height). Features are therefore computed in an
  aspect corrected space (``x * aspect``, ``y``) where distances are isotropic
  and independent of the resolution.
* Every length is normalised by the palm scale, which makes thresholds
  independent of hand size and of the distance from the camera.
* Only the 2D projection is used. The MediaPipe ``z`` estimate is significantly
  noisier than the image plane coordinates and using it was measured to flip
  classification on genuine poses, so it is deliberately ignored here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence, Tuple

from app.hand_tracking import FINGER_JOINTS, WRIST, Landmark

Point = Tuple[float, float]

# Index of the middle finger MCP joint: the palm anchor used for pinch checks.
MIDDLE_MCP = FINGER_JOINTS["MIDDLE"][0]

# Knuckle ring plus the wrist, used to locate the palm centre.
PALM_POINTS: Tuple[int, ...] = (
    WRIST,
    FINGER_JOINTS["INDEX"][0],
    MIDDLE_MCP,
    FINGER_JOINTS["RING"][0],
    FINGER_JOINTS["PINKY"][0],
)

# Ordered fingers whose extension is scored (the thumb is handled separately
# because its pose varies naturally between individuals and poses).
SCORED_FINGERS: Tuple[str, ...] = ("INDEX", "MIDDLE", "RING", "PINKY")

# Minimum palm scale, guarding against degenerate landmark sets.
_MIN_SCALE = 1e-4


def distance(a: Point, b: Point) -> float:
    """Euclidean distance between two 2D points."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def joint_angle(a: Point, b: Point, c: Point) -> float:
    """Interior angle at ``b`` formed by ``a``-``b``-``c``, in degrees."""
    bax = a[0] - b[0]
    bay = a[1] - b[1]
    bcx = c[0] - b[0]
    bcy = c[1] - b[1]
    nba = math.sqrt(bax * bax + bay * bay)
    nbc = math.sqrt(bcx * bcx + bcy * bcy)
    if nba < 1e-9 or nbc < 1e-9:
        return 0.0
    cosine = (bax * bcx + bay * bcy) / (nba * nbc)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def ramp(value: float, low: float, high: float) -> float:
    """Linear 0..1 ramp, returning 0 at ``low`` and 1 at ``high``."""
    if high <= low:
        return 1.0 if value >= high else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


@dataclass(frozen=True, slots=True)
class HandFeatures:
    """Geometric description of a single hand pose."""

    scale: float                                  # palm scale, normalised units
    extensions: Tuple[float, float, float, float]  # index, middle, ring, pinky (0..1)
    thumb_extension: float
    pinch_ratio: float                            # thumb tip to index tip / scale
    pinch_lift: float                             # index tip to palm anchor / scale
    palm_center: Point
    index_tip: Point
    thumb_tip: Point

    @property
    def ordered_extensions(self) -> Tuple[float, float, float, float]:
        """Finger extensions sorted from most to least extended."""
        first, second, third, fourth = sorted(self.extensions, reverse=True)
        return (first, second, third, fourth)

    @property
    def third_extension(self) -> float:
        """Third highest extension: tolerates one poorly tracked finger."""
        return self.ordered_extensions[2]

    @property
    def extension_sum(self) -> float:
        return sum(self.extensions)

    @property
    def mean_extension(self) -> float:
        return sum(self.extensions) / len(self.extensions)


def _finger_extension(
    wrist: Point,
    mcp: Point,
    pip: Point,
    dip: Point,
    tip: Point,
) -> float:
    """Score how extended one finger is, from 0 (fully curled) to 1 (straight).

    Two independent, scale free measurements are combined:

    ``reach``  - wrist to tip distance over wrist to PIP distance. A straight
                 finger is roughly twice as far from the wrist as its PIP joint,
                 a curled finger brings the tip back towards the palm.
    ``angle``  - the PIP joint angle. Straight fingers sit near 180 degrees,
                 curled fingers collapse towards 90 degrees or below.

    Requiring agreement between both keeps the classifier robust when a single
    measurement is distorted by perspective.
    """
    wrist_to_pip = distance(wrist, pip)
    reach = distance(wrist, tip) / wrist_to_pip if wrist_to_pip > _MIN_SCALE else 0.0
    angle = joint_angle(mcp, pip, tip)
    return 0.6 * ramp(reach, 1.00, 1.28) + 0.4 * ramp(angle, 115.0, 160.0)


def extract(landmarks: Sequence[Landmark], aspect: float = 1.0) -> HandFeatures:
    """Build :class:`HandFeatures` from a tracked hand's 21 landmarks.

    ``aspect`` is the frame width divided by its frame height, used to undo the
    anisotropic normalisation of the landmark coordinates.
    """
    aspect = aspect if aspect > 1e-3 else 1.0
    points: Tuple[Point, ...] = tuple((lm.x * aspect, lm.y) for lm in landmarks)

    wrist = points[WRIST]
    middle_mcp = points[MIDDLE_MCP]
    index_mcp = points[FINGER_JOINTS["INDEX"][0]]
    pinky_mcp = points[FINGER_JOINTS["PINKY"][0]]

    # Palm scale: mean of the wrist to middle MCP length and the index to pinky
    # knuckle span. Both are pose independent, so the value only tracks hand size
    # and camera distance.
    scale = max(
        _MIN_SCALE,
        0.5 * (distance(wrist, middle_mcp) + distance(index_mcp, pinky_mcp)),
    )

    extensions = []
    for name in SCORED_FINGERS:
        mcp, pip, dip, tip = (points[i] for i in FINGER_JOINTS[name])
        extensions.append(_finger_extension(wrist, mcp, pip, dip, tip))

    # Thumb spread: the CMC-to-tip angle opens when the thumb is abducted.
    thumb_cmc, _thumb_mcp, _thumb_ip, thumb_tip = (
        points[i] for i in FINGER_JOINTS["THUMB"]
    )
    thumb_extension = ramp(joint_angle(points[WRIST], thumb_cmc, thumb_tip), 100.0, 155.0)

    index_tip = points[FINGER_JOINTS["INDEX"][3]]

    palm_center = (
        sum(points[i][0] for i in PALM_POINTS) / len(PALM_POINTS),
        sum(points[i][1] for i in PALM_POINTS) / len(PALM_POINTS),
    )

    return HandFeatures(
        scale=scale,
        extensions=(extensions[0], extensions[1], extensions[2], extensions[3]),
        thumb_extension=thumb_extension,
        pinch_ratio=distance(thumb_tip, index_tip) / scale,
        # Distance from the pinch point to the palm anchor: a real pinch happens
        # away from the palm, whereas a tucked thumb inside a fist folds both
        # tips onto the palm and fails this check.
        pinch_lift=distance(index_tip, middle_mcp) / scale,
        palm_center=palm_center,
        index_tip=index_tip,
        thumb_tip=thumb_tip,
    )
