"""The VisionCore state block handed to the assistant.

This is the *only* thing about the machine that ever leaves the process when the
user sends a message. It is deliberately built by hand from telemetry that is
already on the HUD - never a camera frame, never an image, never audio, never a
file path, never a window title, never anything about other applications.

Keeping it small also keeps the assistant honest: if a value is not in here the
model has nothing to guess from, and the panel's local answers read from exactly
the same object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from app.controls.safety import ControlMode, ControlState
from app.gestures.types import Gesture, GesturePhase
from app.hand_tracking import TrackingState
from app.voice.types import VoiceState

if TYPE_CHECKING:  # avoids an import cycle: app.state imports the AI snapshot
    from app.state import Telemetry

# Phase 6 keeps at most eight rows; the assistant only needs the recent few.
RECENT_ACTION_LIMIT = 5


@dataclass(frozen=True, slots=True)
class AIContext:
    """A minimal, sanitized description of what VisionCore is doing right now."""

    camera_state: str = "UNKNOWN"
    camera_detail: str = ""
    tracking_state: str = "UNKNOWN"
    tracking_confidence: Optional[float] = None
    hand_count: int = 0
    max_hands: int = 1
    gesture: str = "NONE"
    gesture_phase: str = "NONE"
    gesture_confidence: Optional[float] = None
    control_mode: str = "MOUSE"
    control_state: str = "DISABLED"
    control_detail: str = ""
    pointer_active: bool = False
    dragging: bool = False
    scrolling: bool = False
    device_state: str = "DISABLED"
    device_detail: str = ""
    device_action_count: int = 0
    capabilities: Tuple[Tuple[str, bool, str], ...] = ()
    recent_actions: Tuple[str, ...] = ()
    render_fps: float = 0.0
    errors: Tuple[str, ...] = ()
    quality: str = "SEARCHING"
    tracking_engine: str = "UNKNOWN"
    emergency_stops: int = 0
    # Voice input state (Phase 8). The microphone is only ever open because the
    # user asked for it; "OFF" is the truthful default.
    voice_state: str = VoiceState.OFF.value
    voice_engine: str = "NONE"
    voice_detail: str = ""
    voice_available: bool = False
    # Which input the current message arrived on (TEXT / VOICE / ...), so a
    # transcript can be treated as one (it may contain recognition errors).
    input_source: str = "TEXT"
    # One deterministic clause describing what the user is doing right now,
    # derived from real tracking and mode values - never guessed.
    activity: str = "nothing is being tracked yet"

    # -- rendering --------------------------------------------------------- #

    def prompt_text(self) -> str:
        """Compact plain-text form sent with a provider request."""
        lines = [
            f"camera: {self.camera_state}{self._suffix(self.camera_detail)}",
            f"tracking: {self.tracking_state} ({self.tracking_engine})",
            f"tracking_confidence: {self._percent(self.tracking_confidence)}",
            f"hands_visible: {self.hand_count} (max {self.max_hands})",
            f"quality: {self.quality}",
            f"gesture: {self.gesture} (phase {self.gesture_phase}, "
            f"confidence {self._percent(self.gesture_confidence)})",
            f"control_mode: {self.control_mode}",
            f"mouse_control: {self.control_state}{self._suffix(self.control_detail)}",
            f"pointer: active={self.pointer_active} dragging={self.dragging} "
            f"scrolling={self.scrolling}",
            f"device_control: {self.device_state}{self._suffix(self.device_detail)}",
            "device_capabilities: "
            + (
                ", ".join(
                    f"{name}={'available' if ok else 'unavailable'}"
                    + (f" ({note})" if note and not ok else "")
                    for name, ok, note in self.capabilities
                )
                or "none reported"
            ),
            "recent_actions: "
            + (", ".join(self.recent_actions) if self.recent_actions else "none"),
            f"activity: {self.activity}",
            "voice_input: "
            + self.voice_state
            + (f" (engine {self.voice_engine})" if self.voice_available else " (no engine)")
            + self._suffix(self.voice_detail),
            f"input_source: {self.input_source}",
            f"device_actions_performed: {self.device_action_count}",
            f"emergency_stops_this_session: {self.emergency_stops}",
            f"render_fps: {self.render_fps:.0f}" if self.render_fps > 0 else "render_fps: unknown",
        ]
        if self.errors:
            lines.append("errors: " + "; ".join(self.errors))
        return "\n".join(lines)

    def summary_line(self) -> str:
        """One-line summary used by the panel's status strip."""
        return (
            f"{self.control_mode} MODE | {self.tracking_state} | "
            f"GESTURE {self.gesture} | HANDS {self.hand_count}"
        )

    # -- gate helpers ------------------------------------------------------ #
    # The state strings below are produced from the real enums, so a gate that
    # compares against them can never drift from the controllers.

    @property
    def emergency(self) -> bool:
        """True while either control layer is in an emergency stop."""
        return (
            self.control_state == ControlState.EMERGENCY_STOP.label
            or self.device_state == ControlState.EMERGENCY_STOP.label
        )

    @property
    def mouse_enabled(self) -> bool:
        """True while the mouse layer can act (arming or active)."""
        return self.control_state in (ControlState.ARMED.label, ControlState.ACTIVE.label)

    @property
    def mouse_paused(self) -> bool:
        return self.control_state == ControlState.PAUSED.label

    @property
    def device_enabled(self) -> bool:
        """True while the device layer can act (arming or active)."""
        return self.device_state in (ControlState.ARMED.label, ControlState.ACTIVE.label)

    def capability_map(self) -> Dict[str, bool]:
        """Capability name -> availability, as reported by the platform."""
        return {name: available for name, available, _ in self.capabilities}

    @staticmethod
    def _percent(value: Optional[float]) -> str:
        return f"{value * 100:.0f}%" if value is not None else "unknown"

    @staticmethod
    def _suffix(detail: str) -> str:
        return f" ({detail})" if detail else ""


def build_context(telemetry: "Telemetry") -> AIContext:
    """Snapshot the real, HUD-visible state into a context object."""
    camera_detail = (
        f"{telemetry.camera_width}x{telemetry.camera_height} @ "
        f"{telemetry.camera_fps:.0f} fps, backend {telemetry.camera_backend}"
        if telemetry.camera_width > 0
        else (telemetry.error_message or "")
    )

    errors = []
    if telemetry.tracking_error:
        errors.append(f"tracking engine: {telemetry.tracking_error}")
    if telemetry.error_title:
        errors.append(str(telemetry.error_title))
    if telemetry.interaction.error is not None:
        errors.append(telemetry.interaction.error.title)

    capabilities = tuple(
        sorted(
            (name, bool(ok), telemetry.device_capability_notes.get(name, ""))
            for name, ok in telemetry.device_capabilities.items()
        )
    )
    recent = tuple(entry.display_label for entry in telemetry.feedback[:RECENT_ACTION_LIMIT])

    voice = telemetry.voice
    return AIContext(
        camera_state=str(telemetry.camera.value),
        camera_detail=str(camera_detail)[:120],
        tracking_state=telemetry.tracking_state.value,
        tracking_confidence=telemetry.hand_confidence,
        hand_count=telemetry.hands_detected,
        max_hands=telemetry.max_hands,
        gesture=telemetry.gesture.value,
        gesture_phase=telemetry.gesture_phase.value,
        gesture_confidence=telemetry.gesture_confidence,
        control_mode=telemetry.control_mode.value,
        control_state=telemetry.control_state.label,
        control_detail=telemetry.control_message,
        pointer_active=telemetry.pointer_active,
        dragging=telemetry.pointer_dragging,
        scrolling=telemetry.pointer_scrolling,
        device_state=telemetry.device_state.label,
        device_detail=telemetry.device_message,
        device_action_count=telemetry.device_actions,
        capabilities=capabilities,
        recent_actions=recent,
        render_fps=telemetry.render_fps,
        errors=tuple(errors[:3]),
        quality=telemetry.interaction.quality.label,
        tracking_engine=telemetry.tracking_engine,
        emergency_stops=telemetry.control_emergency_stops,
        voice_state=voice.state.value,
        voice_engine=voice.engine,
        voice_detail=voice.note,
        voice_available=voice.available,
        activity=describe_activity(
            gesture=telemetry.gesture.value,
            mode=telemetry.control_mode.value,
            tracking_state=telemetry.tracking_state.value,
            hands=telemetry.hands_detected,
        ),
    )


# --------------------------------------------------------------------------- #
# Activity description: what the user is doing, from real values only
# --------------------------------------------------------------------------- #

# What each pose means *in a given mode*. Nothing here is invented: every entry
# is the gesture mapping the control layers actually implement.
_ACTIVITY_PHRASES: Dict[str, Tuple[str, str]] = {
    # gesture: (MOUSE mode phrasing, DEVICE mode phrasing)
    Gesture.POINT.value: ("pointing", "pointing (the pointer layer is idle in DEVICE mode)"),
    Gesture.PINCH.value: (
        "pinching",
        "pinching (a pinch toggles mute once in DEVICE mode)",
    ),
    Gesture.TWO_FINGER.value: (
        "holding two fingers",
        "holding two fingers (vertical travel is volume in DEVICE mode)",
    ),
    Gesture.FIST.value: (
        "holding a fist (no action in MOUSE mode)",
        "holding a fist (vertical travel is brightness in DEVICE mode)",
    ),
    Gesture.OPEN_PALM.value: (
        "holding an open palm (held still it trips the emergency stop)",
        "holding an open palm (a brief palm is play/pause; held it stops everything)",
    ),
    Gesture.SWIPE_LEFT.value: (
        "swiping left (no action in MOUSE mode)",
        "swiping left (previous track in DEVICE mode)",
    ),
    Gesture.SWIPE_RIGHT.value: (
        "swiping right (no action in MOUSE mode)",
        "swiping right (next track in DEVICE mode)",
    ),
}


def describe_activity(
    gesture: str,
    mode: str,
    tracking_state: str,
    hands: int,
) -> str:
    """One clause describing the current activity from real state only.

    Used by the assistant (and shown in its state block), so an answer like
    "you are currently pointing in MOUSE mode" is something VisionCore measured
    rather than something a model guessed.
    """
    if hands <= 0 or tracking_state not in (
        TrackingState.TRACKING.value,
        TrackingState.DETECTING.value,
    ):
        if tracking_state == TrackingState.HAND_LOST.value:
            return "no hand is being tracked (the last hand was lost)"
        return "no hand is being tracked right now"
    if gesture == Gesture.NONE.value:
        return f"holding no recognised gesture in {mode} mode"
    mouse_phrase, device_phrase = _ACTIVITY_PHRASES.get(
        gesture, (f"showing {gesture}", f"showing {gesture}")
    )
    phrase = device_phrase if mode == ControlMode.DEVICE.value else mouse_phrase
    if mode == ControlMode.DEVICE.value and gesture not in _ACTIVITY_PHRASES:
        return f"showing {gesture} in DEVICE mode"
    if mode not in (ControlMode.MOUSE.value, ControlMode.DEVICE.value):
        return f"showing {gesture}"
    return f"{phrase} in {mode} mode"


# --------------------------------------------------------------------------- #
# Frozen capability vocabulary, so explanations can never drift from the code
# --------------------------------------------------------------------------- #


def tracked_gestures() -> Tuple[Tuple[str, str], ...]:
    """The real gesture vocabulary and what each one actually does."""
    return (
        (Gesture.POINT.value, "moves the pointer with the index fingertip"),
        (Gesture.PINCH.value, "clicks, and holding it drags"),
        (Gesture.TWO_FINGER.value, "scrolls (volume in DEVICE mode)"),
        (Gesture.OPEN_PALM.value, "held still for a moment, trips the emergency stop"),
        (Gesture.FIST.value, "no action in MOUSE mode (brightness in DEVICE mode)"),
        (Gesture.SWIPE_LEFT.value, "previous track in DEVICE mode"),
        (Gesture.SWIPE_RIGHT.value, "next track in DEVICE mode"),
    )


def tracking_states() -> Tuple[Tuple[str, str], ...]:
    """The real tracking states and their HUD labels."""
    return tuple(
        (state.value, state.status_label)
        for state in (
            TrackingState.NO_HAND,
            TrackingState.DETECTING,
            TrackingState.TRACKING,
            TrackingState.HAND_LOST,
        )
    )


def gesture_phases() -> Tuple[str, ...]:
    """The real gesture phases (NONE / START / ACTIVE / RELEASE)."""
    return tuple(phase.value for phase in GesturePhase)
