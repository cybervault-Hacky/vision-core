"""Deterministic local answers - the honest offline mode.

When no provider is configured (or a provider fails), VisionCore can still
answer questions about *itself*, because the answer is somewhere in its own
telemetry. These answers are computed, not generated: they read the real
:class:`~app.ai.context.AIContext`, they never guess, and they are tagged with
:data:`~app.ai.types.ResponseSource.LOCAL` so the interface can show that no
model was involved.

The rules:

* only real values are quoted; an unknown value is reported as unknown;
* a question outside VisionCore's own state is answered with the honest
  "no provider is configured" note instead of an invention;
* nothing here claims to be a language model, and nothing pretends to know
  anything about the machine beyond what the context carries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

from app.ai.context import (
    AIContext,
    gesture_phases,
    tracked_gestures,
    tracking_states,
)
from app.ai.router import describe_actions
from app.ai.commands import spoken_examples

_NO_PROVIDER_NOTE = (
    "No AI provider is configured, so this is a local, deterministic answer rather "
    "than a model reply. I can still answer questions about VisionCore's own state "
    "(camera, tracking, gesture, mode, recent actions) and carry out the plain "
    "commands I recognise literally - for example \"turn the volume up\" or "
    "\"put me in device mode\". Anything freer needs a configured provider: set "
    "VISIONCORE_AI_PROVIDER and VISIONCORE_AI_API_KEY to enable one."
)

_PUNCTUATION = re.compile(r"[^a-z0-9\s']")


@dataclass(frozen=True, slots=True)
class LocalAnswer:
    """A computed answer plus the topic that matched."""

    topic: str
    text: str


def _normalise(message: str) -> str:
    text = _PUNCTUATION.sub(" ", (message or "").lower())
    return " ".join(text.split())


def _has(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def _percent(value: Optional[float]) -> str:
    return f"{value * 100:.0f}%" if value is not None else "unknown"


# --------------------------------------------------------------------------- #
# Individual answers
# --------------------------------------------------------------------------- #


def _cursor_answer(ctx: AIContext) -> str:
    """Diagnose the pointer from real state, most blocking cause first."""
    if ctx.emergency:
        return (
            "Control is in EMERGENCY STOP, so the pointer is released. "
            "The camera and gesture detection keep running; press C or the CONTROL "
            "button in the sidebar to reset the safety state."
        )
    if ctx.control_state == "DISABLED":
        return (
            "The control layer is DISABLED, so no gesture moves the pointer. "
            f"Backend status: {ctx.control_detail or 'not reported'}. "
            "Press C to enable control."
        )
    if ctx.control_state == "PAUSED":
        return "Control is PAUSED, so the pointer is standing still. Press C to resume."
    if ctx.control_mode != "MOUSE":
        return (
            f"You are in {ctx.control_mode} mode, where gestures drive device controls "
            "instead of the pointer. Press M for mouse mode."
        )
    if ctx.hand_count == 0:
        return (
            f"No hand is visible, so the pointer has nothing to follow "
            f"(tracking state {ctx.tracking_state}, quality {ctx.quality}). "
            "Bring one hand fully into the camera frame."
        )
    if ctx.gesture != "POINT":
        return (
            f"Tracking is {ctx.tracking_state} with {ctx.hand_count} hand(s), but the "
            f"current gesture is {ctx.gesture}, and only POINT moves the cursor. "
            "Extend the index finger on its own."
        )
    if not ctx.pointer_active:
        return (
            "The POINT pose is recognised but the pointer has not engaged yet; the gate "
            f"needs a stable, confident lock (confidence {_percent(ctx.tracking_confidence)}, "
            f"quality {ctx.quality})."
        )
    return (
        "The pointer is active, so the cursor should be following your index finger. "
        f"Last reported control message: {ctx.control_detail or 'none'}."
    )


def _mode_answer(ctx: AIContext) -> str:
    if ctx.control_mode == "DEVICE":
        return (
            "You are in DEVICE mode: two-finger travel changes volume, swipes change "
            "tracks and a fist changes brightness. Press M to return to mouse control."
        )
    return (
        "You are in MOUSE mode: the index finger moves the pointer, a pinch clicks and "
        "two fingers scroll. Press D for device control."
    )


def _tracking_answer(ctx: AIContext) -> str:
    if ctx.tracking_state == "TRACKING":
        return (
            f"Tracking is locked on {ctx.hand_count} hand(s) at {_percent(ctx.tracking_confidence)} "
            f"confidence (quality {ctx.quality})."
        )
    if ctx.tracking_state in ("DETECTING", "HAND_LOST"):
        return (
            f"Tracking is {ctx.tracking_state} - a hand was seen but the lock is not stable "
            f"(quality {ctx.quality}, confidence {_percent(ctx.tracking_confidence)}). "
            "Hold your hand still inside the frame."
        )
    return (
        f"Tracking is {ctx.tracking_state}: no hand is being followed right now "
        f"(quality {ctx.quality}). Engine: {ctx.tracking_engine}."
    )


def _gesture_answer(ctx: AIContext) -> str:
    if ctx.gesture == "NONE":
        return (
            "No gesture is currently detected; the engine is searching "
            f"(tracking {ctx.tracking_state}, {ctx.hand_count} hand(s))."
        )
    return (
        f"The current gesture is {ctx.gesture} in the {ctx.gesture_phase} phase at "
        f"{_percent(ctx.gesture_confidence)} confidence."
    )


def _activity_answer(ctx: AIContext) -> str:
    """'What am I doing right now?' - answered from measured state only."""
    if ctx.hand_count <= 0 or ctx.activity.startswith("no hand"):
        return (
            f"There is nothing to describe yet: {ctx.activity}. Tracking is "
            f"{ctx.tracking_state} (quality {ctx.quality}); bring one hand into the "
            "camera frame and hold it still."
        )
    if ctx.gesture == "NONE":
        detail = (
            f" No pose is held right now (tracking confidence "
            f"{_percent(ctx.tracking_confidence)}, quality {ctx.quality})."
        )
    else:
        detail = (
            f" The pose is {ctx.gesture}, phase {ctx.gesture_phase}, at "
            f"{_percent(ctx.gesture_confidence)} confidence."
        )
        for name, description in tracked_gestures():
            if name == ctx.gesture:
                detail += f" In this mode it {description}."
                break
    return f"You are currently {ctx.activity}.{detail}"


def _voice_answer(ctx: AIContext) -> str:
    """Microphone state, told exactly as it is."""
    state = ctx.voice_state
    if state == "LISTENING":
        return (
            "The microphone is open right now (LISTENING). Speak your request, or "
            "press V again to cancel it. Nothing is recorded to disk and no audio "
            "leaves this machine."
        )
    if state == "PROCESSING":
        return "Audio has been captured and is being transcribed locally."
    if state == "READY":
        return "The last transcription finished; the microphone is closed again."
    if state == "ERROR":
        return f"Voice input reported an error: {ctx.voice_detail or 'engine failure'}."
    if state == "UNAVAILABLE" or not ctx.voice_available:
        return (
            f"Voice input is unavailable on this machine: "
            f"{ctx.voice_detail or 'no local speech engine or microphone'}. "
            "Typed messages and every gesture still work exactly as before."
        )
    return (
        f"The microphone is OFF (engine {ctx.voice_engine}). Press V or the "
        "microphone control in the assistant panel to listen; it only opens when "
        "you ask for it."
    )


def _camera_answer(ctx: AIContext) -> str:
    if ctx.camera_state == "ONLINE":
        detail = ctx.camera_detail or "resolution not reported"
        return f"The camera is ONLINE: {detail}."
    if ctx.camera_state == "CHECKING":
        return "The camera is still being opened (CHECKING)."
    return (
        f"The camera reports {ctx.camera_state}. "
        f"{ctx.camera_detail or 'No further detail is available.'}"
    )


def _recent_answer(ctx: AIContext) -> str:
    if not ctx.recent_actions:
        return "No actions have been performed in this session yet."
    listed = ", ".join(ctx.recent_actions)
    return f"Your most recent actions, newest first: {listed}."


def _stopped_answer(ctx: AIContext) -> str:
    if ctx.emergency:
        reason = ctx.control_detail or "an emergency stop was tripped"
        return (
            f"Control stopped because an emergency stop is active ({reason}). "
            "Holding an open palm still for a moment trips it; press C or use the CONTROL "
            "button to reset."
        )
    if ctx.control_state == "PAUSED":
        return "Control is paused, not stopped: gestures are still tracked but no action runs."
    if ctx.control_state == "DISABLED":
        return "Control is disabled, so nothing was stopped after the fact; it was never enabled."
    if ctx.control_detail:
        return f"The control layer reports: {ctx.control_detail}."
    return "Both control layers are running, so nothing has stopped them."


def _gesture_help_answer(_ctx: AIContext) -> str:
    lines = [f"- {name}: {description}" for name, description in tracked_gestures()]
    phases = " / ".join(gesture_phases())
    return (
        "These are the gestures VisionCore recognises, and what each one does:\n"
        + "\n".join(lines)
        + f"\nEach gesture is reported in three phases: {phases}."
    )


def _tracking_help_answer(_ctx: AIContext) -> str:
    lines = [f"- {name}: {label}" for name, label in tracking_states()]
    return "Tracking moves through these states:\n" + "\n".join(lines)


def _capability_answer(ctx: AIContext) -> str:
    parts = [
        "I can answer questions about VisionCore's own state and request a single "
        "allowlisted action through the normal safety gates.",
        f"Actions available: {describe_actions()}.",
        "Plain commands I recognise without a provider: "
        + ", ".join(f'"{example}"' for example in spoken_examples())
        + ".",
        (
            "Voice input is available: press V to speak."
            if ctx.voice_available
            else "Voice input is unavailable here, so please type instead."
        ),
    ]
    if ctx.capabilities:
        offered = ", ".join(
            f"{name} {'READY' if ok else 'UNAVAILABLE'}" for name, ok, _ in ctx.capabilities
        )
        parts.append(f"Platform capabilities reported right now: {offered}.")
    parts.append("System changes such as shutting down, restarting or running commands are not available.")
    return " ".join(parts)


def _identity_answer(_ctx: AIContext) -> str:
    return (
        "I am VisionCore's built-in assistant. This reply was produced locally and "
        "deterministically from VisionCore's own telemetry, not by a language model. "
        "I only report values VisionCore actually measured, and system changes such as "
        "shutting down, restarting or running commands are not available through me."
    )


def _performance_answer(ctx: AIContext) -> str:
    fps = f"{ctx.render_fps:.0f} FPS" if ctx.render_fps > 0 else "not measured yet"
    return (
        f"Rendering runs at {fps}. Every AI request is handled on a background thread, "
        "so asking a question never stops the camera or the gesture pipeline."
    )


def _greeting_answer(ctx: AIContext) -> str:
    return (
        f"VisionCore is running: {ctx.summary_line().lower()}. "
        "Ask about the camera, tracking, gestures or the current mode, or ask for an "
        "allowlisted action such as turning the volume up."
    )


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

# Order matters: the first match wins, most specific first.
_RULES: Sequence[Tuple[str, Tuple[str, ...], Callable[[AIContext], str]]] = (
    (
        "cursor",
        (
            "cursor",
            "pointer",
            "mouse is not",
            "mouse not",
            "mouse is stuck",
            "cannot move",
            "can't move",
            "why is the mouse",
        ),
        _cursor_answer,
    ),
    (
        "stopped",
        (
            "why did the system stop",
            "why did it stop",
            "why did control stop",
            "why was control stopped",
            "why did it pause",
            "what stopped",
            "emergency stop",
        ),
        _stopped_answer,
    ),
    (
        "recent",
        (
            "what did i just do",
            "what did i do",
            "recent action",
            "last action",
            "what have i done",
            "action history",
        ),
        _recent_answer,
    ),
    (
        "mode",
        (
            "what mode",
            "which mode",
            "current mode",
            "am i in device",
            "am i in mouse",
            "mode am i",
        ),
        _mode_answer,
    ),
    (
        "tracking",
        (
            "tracking active",
            "is tracking",
            "is my hand tracked",
            "tracking working",
            "tracking status",
            "is it tracking",
            "does tracking",
        ),
        _tracking_answer,
    ),
    (
        "tracking_help",
        ("tracking state", "tracking states"),
        _tracking_help_answer,
    ),
    (
        "activity",
        (
            "what am i doing",
            "what is my hand doing",
            "what's my hand doing",
            "what am i holding",
            "what am i showing",
            "what am i currently doing",
            "describe what i am doing",
        ),
        _activity_answer,
    ),
    (
        "voice",
        (
            "microphone",
            "mic on",
            "mic off",
            "listening",
            "can you hear me",
            "are you listening",
            "voice input",
            "voice control",
            "speech input",
        ),
        _voice_answer,
    ),
    (
        "gesture",
        (
            "what gesture",
            "which gesture",
            "gesture detected",
            "gesture am i",
            "current gesture",
        ),
        _gesture_answer,
    ),
    (
        "gesture_help",
        (
            "how do gestures work",
            "how does pinch work",
            "how do i pinch",
            "how do i click",
            "how do i drag",
            "how do i scroll",
            "which gestures",
            "what gestures",
            "gesture list",
        ),
        _gesture_help_answer,
    ),
    (
        "camera",
        (
            "camera",
            "webcam",
        ),
        _camera_answer,
    ),
    (
        "capabilities",
        (
            "what can you do",
            "what can i ask",
            "what can you control",
            "help",
            "commands",
            "capabilities",
        ),
        _capability_answer,
    ),
    (
        "performance",
        ("fps", "framerate", "frame rate", "performance", "how fast"),
        _performance_answer,
    ),
    (
        "identity",
        (
            "who are you",
            "what are you",
            "are you an ai",
            "are you a language model",
            "are you chatgpt",
            "which model",
            "are you connected",
            "are you online",
        ),
        _identity_answer,
    ),
    (
        "greeting",
        ("hello", "hi", "hey", "good morning", "good evening"),
        _greeting_answer,
    ),
)


def answer(message: str, ctx: AIContext) -> Optional[LocalAnswer]:
    """Return a deterministic answer for a state question, or ``None``."""
    text = _normalise(message)
    if not text:
        return None
    for topic, phrases, handler in _RULES:
        if _has(text, *phrases):
            return LocalAnswer(topic=topic, text=handler(ctx))
    return None


def off_topic_answer() -> str:
    """Honest reply for anything local mode cannot answer.

    Nothing is invented and no provider is faked: the user is told exactly why
    the question cannot be answered here and what would change that.
    """
    return _NO_PROVIDER_NOTE


def provider_note(configured: bool) -> str:
    """Header/status note describing what the panel can currently do."""
    if configured:
        return "Provider answers are available; local state answers stay available offline."
    return (
        "No provider configured: state questions are answered locally and "
        "deterministically."
    )
