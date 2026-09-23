"""Local, context aware interaction layer for VisionCore.

This package gives the interface a typed description of what the system is doing
(interaction state, focus readout, status ring, tracking quality), a reusable
action feedback channel with a memory-only recent-action timeline, and one
priority ordered intent router shared by every input source - including the
voice input that is reserved in the architecture but deliberately not
implemented.

Nothing here performs recognition, control or image processing, and nothing here
contacts a network, an online model or a microphone.
"""

from app.interaction.director import InteractionDirector
from app.interaction.feedback import (
    FEEDBACK_DURATION,
    TIMELINE_LIMIT,
    ActionFeedback,
    FeedbackCenter,
    FeedbackSource,
)
from app.interaction.intent import (
    Intent,
    IntentKind,
    IntentOutcome,
    IntentRouter,
    IntentSource,
)
from app.interaction.states import (
    ActionTier,
    FocusPhase,
    FocusReadout,
    InteractionLifecycle,
    InteractionSnapshot,
    InteractionState,
    RecoveryAction,
    RingState,
    SystemError,
    TrackingQuality,
)

__all__ = [
    "ActionFeedback",
    "ActionTier",
    "FEEDBACK_DURATION",
    "FeedbackCenter",
    "FeedbackSource",
    "FocusPhase",
    "FocusReadout",
    "Intent",
    "IntentKind",
    "IntentOutcome",
    "IntentRouter",
    "IntentSource",
    "InteractionDirector",
    "InteractionLifecycle",
    "InteractionSnapshot",
    "InteractionState",
    "RecoveryAction",
    "RingState",
    "SystemError",
    "TIMELINE_LIMIT",
    "TrackingQuality",
]
