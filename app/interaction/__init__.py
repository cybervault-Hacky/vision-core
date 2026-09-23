"""Local, context aware interaction layer for VisionCore.

This package gives the interface a typed description of what the system is doing
(interaction state, focus readout, status ring, tracking quality), a reusable
action feedback channel with a memory-only recent-action timeline, one priority
ordered intent router shared by every input source, and - since Phase 8 - the
unified input model that names those sources (gesture, text, voice, interface,
assistant) and describes what each request carries.

Nothing here performs recognition, control or image processing. Voice is
described here, but the microphone itself lives in :mod:`app.voice`: this layer
only records that a request came from speech rather than from typing.
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
from app.interaction.multimodal import (
    InputEnvelope,
    InputKind,
    InputSource,
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
    "InputEnvelope",
    "InputKind",
    "InputSource",
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
