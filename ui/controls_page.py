"""The Controls workspace: an operational view of every control layer.

The page is informational and operational at once. Each section reports the real
state of one layer - mouse control, device control, gestures, safety - and
offers exactly the actions the underlying engine can actually execute. A
capability the platform does not provide is shown as unavailable with its real
reason, never as a working control.

Every button raises a token the window routes to the existing application
callbacks. Nothing on this page performs an action itself.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from app.controls.safety import ControlMode, ControlState
from app.state import Telemetry
from ui.feedback_view import FeedbackView
from ui.section_page import SectionPage
from ui.theme import (
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_DISABLED,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_WARNING,
    control_state_color,
)

ROW_HEIGHT = 44


class ControlsPage(SectionPage):
    """Sections of control rows built from live telemetry."""

    title = "Controls"

    def __init__(self) -> None:
        super().__init__()
        self._feedback_view = FeedbackView()

    # -- section content --------------------------------------------------- #

    def _build_sections(self, telemetry: Telemetry, fonts) -> List[Tuple[str, str, List[dict]]]:
        return [
            ("Mouse", "mouse", self._mouse_rows(telemetry)),
            ("Device", "monitor", self._device_rows(telemetry)),
            ("Gestures", "hand", self._gesture_rows(telemetry)),
            ("Safety", "shield", self._safety_rows(telemetry)),
            ("Activity", "clock", self._activity_rows(telemetry)),
        ]

    def _mouse_rows(self, telemetry: Telemetry) -> List[dict]:
        state = telemetry.control_state
        device_mode = telemetry.control_mode is ControlMode.DEVICE
        available = telemetry.control_available

        readiness = self._readiness(telemetry, state, device_mode)
        primary_label = self._toggle_label(state, available)
        return [
            {
                "name": "Control mode",
                "desc": "Which layer may act on gestures",
                "height": ROW_HEIGHT + 8,
                "segmented": (
                    ("Mouse", "Device"),
                    1 if device_mode else 0,
                    ("mode:MOUSE", "mode:DEVICE"),
                ),
            },
            {"name": "Pointer", "desc": "Cursor follows the index finger",
             "height": ROW_HEIGHT, "value": readiness["pointer"]},
            {"name": "Click", "desc": "Pinch to click, hold to drag",
             "height": ROW_HEIGHT, "value": readiness["click"]},
            {"name": "Scroll", "desc": "Two-finger vertical travel",
             "height": ROW_HEIGHT, "value": readiness["scroll"]},
            {
                "name": "Mouse control",
                "desc": "Touchless mouse layer",
                "height": ROW_HEIGHT + 8,
                "value": (
                    state.label.replace("_", " ").capitalize(),
                    control_state_color(state),
                ),
                "buttons": [
                    ("toggle", primary_label, "primary", None,
                     available or state is not ControlState.DISABLED),
                    ("disable", "Disable", "ghost", None,
                     state is not ControlState.DISABLED),
                ],
                "button_width": 92,
            },
        ]

    @staticmethod
    def _toggle_label(state: ControlState, available: bool) -> str:
        if state is ControlState.DISABLED:
            return "Enable" if available else "Unavailable"
        if state in (ControlState.ARMED, ControlState.ACTIVE):
            return "Pause"
        return "Resume"

    @staticmethod
    def _readiness(
        telemetry: Telemetry, state: ControlState, device_mode: bool
    ) -> Dict[str, Tuple[str, Tuple[int, int, int]]]:
        """Real readiness of pointer, click and scroll for the active mode."""

        def readiness_for(active: bool, active_label: str):
            if state is ControlState.EMERGENCY_STOP:
                return ("Blocked", COLOR_DANGER)
            if device_mode:
                return ("Suspended", COLOR_DISABLED)
            if active:
                return (active_label, COLOR_SUCCESS)
            if not telemetry.control_available:
                return ("Unavailable", COLOR_DISABLED)
            if state is ControlState.PAUSED:
                return ("Paused", COLOR_WARNING)
            if state in (ControlState.ARMED, ControlState.ACTIVE):
                return ("Ready", COLOR_ACCENT)
            return ("Off", COLOR_DISABLED)

        return {
            "pointer": readiness_for(telemetry.pointer_active, "Active"),
            "click": readiness_for(telemetry.pointer_dragging, "Dragging"),
            "scroll": readiness_for(telemetry.pointer_scrolling, "Active"),
        }

    def _device_rows(self, telemetry: Telemetry) -> List[dict]:
        capabilities = telemetry.device_capabilities
        device_mode = telemetry.control_mode is ControlMode.DEVICE
        state = telemetry.device_state
        available = telemetry.device_available

        def cap(key: str) -> bool:
            return bool(capabilities.get(key))

        def note(key: str) -> str:
            return telemetry.device_capability_notes.get(key, "")

        suspended = (
            "Suspended outside device mode"
            if not device_mode and state is not ControlState.DISABLED
            else ""
        )

        rows = [
            {
                "name": "Device control",
                "desc": "Volume, media, brightness, windows and apps",
                "height": ROW_HEIGHT + 8,
                "value": (
                    state.label.replace("_", " ").capitalize(),
                    control_state_color(state),
                ),
                "buttons": [
                    ("device_toggle", self._toggle_label(state, available), "primary",
                     None, available or state is not ControlState.DISABLED),
                ],
                "button_width": 96,
            },
            {
                "name": "Volume",
                "desc": suspended or note("VOLUME") or "Two-finger travel in device mode",
                "height": ROW_HEIGHT + 8,
                "value": self._volume_text(telemetry, cap("VOLUME")),
                "buttons": [
                    ("device:VOLUME_DOWN", "", "default", "chevron_down", cap("VOLUME")),
                    ("device:MUTE", "", "default",
                     "volume" if not telemetry.device_muted else "mute", cap("VOLUME")),
                    ("device:VOLUME_UP", "", "default", "chevron_up", cap("VOLUME")),
                ],
                "button_width": 40,
            },
            {
                "name": "Media",
                "desc": suspended or note("MEDIA") or "Swipes skip tracks in device mode",
                "height": ROW_HEIGHT + 8,
                "value": (
                    ("Ready", COLOR_SUCCESS) if cap("MEDIA")
                    else ("Unavailable", COLOR_DISABLED)
                ),
                "buttons": [
                    ("device:NEXT_TRACK", "", "default", "skip_next", cap("MEDIA")),
                    ("device:PLAY_PAUSE", "", "default", "play_pause", cap("MEDIA")),
                    ("device:PREVIOUS_TRACK", "", "default", "skip_prev", cap("MEDIA")),
                ],
                "button_width": 40,
            },
            {
                "name": "Brightness",
                "desc": suspended or note("BRIGHTNESS") or "Fist travel in device mode",
                "height": ROW_HEIGHT + 8,
                "value": self._brightness_text(telemetry, cap("BRIGHTNESS")),
                "buttons": [
                    ("device:BRIGHTNESS_DOWN", "", "default", "chevron_down", cap("BRIGHTNESS")),
                    ("device:BRIGHTNESS_UP", "", "default", "chevron_up", cap("BRIGHTNESS")),
                ],
                "button_width": 40,
            },
            {
                "name": "Windows",
                "desc": suspended or note("WINDOW") or "Allowlisted window actions",
                "height": ROW_HEIGHT + 8,
                "value": (
                    ("Ready", COLOR_SUCCESS) if cap("WINDOW")
                    else ("Unavailable", COLOR_DISABLED)
                ),
                "buttons": [
                    ("device:MINIMIZE", "Minimize", "default", None, cap("WINDOW")),
                    ("device:MAXIMIZE", "Maximize", "default", None, cap("WINDOW")),
                    ("device:NEXT_WINDOW", "Switch", "default", None, cap("WINDOW")),
                ],
                "button_width": 92,
            },
        ]

        # Allowlisted applications: only the ones this platform resolved.
        launchable = list(telemetry.device_launchable.items())[:4]
        if launchable:
            launcher_available = cap("LAUNCHER")
            rows.append({
                "name": "Applications",
                "desc": note("LAUNCHER") or "Allowlisted application launcher",
                "height": ROW_HEIGHT + 8,
                "value": (
                    ("Ready", COLOR_SUCCESS) if launcher_available
                    else ("Unavailable", COLOR_DISABLED)
                ),
                "buttons": [
                    (f"launch:{key}", label, "default", "app", launcher_available)
                    for key, label in launchable
                ],
                "button_width": 92,
            })
        return rows

    @staticmethod
    def _volume_text(telemetry: Telemetry, supported: bool) -> Tuple[str, Tuple[int, int, int]]:
        if not supported:
            return ("Unavailable", COLOR_DISABLED)
        if telemetry.device_volume_known and telemetry.device_volume is not None:
            level = f"{telemetry.device_volume * 100:.0f}%"
            if telemetry.device_muted:
                level += " (muted)"
            return (level, COLOR_TEXT)
        steps = telemetry.device_volume_steps
        if steps:
            return (f"{steps:+d} steps", COLOR_TEXT)
        return ("Relative", COLOR_TEXT_DIM)

    @staticmethod
    def _brightness_text(telemetry: Telemetry, supported: bool) -> Tuple[str, Tuple[int, int, int]]:
        if not supported:
            return ("Unavailable", COLOR_DISABLED)
        if telemetry.device_brightness_known and telemetry.device_brightness is not None:
            return (f"{telemetry.device_brightness * 100:.0f}%", COLOR_TEXT)
        return ("Relative", COLOR_TEXT_DIM)

    def _gesture_rows(self, telemetry: Telemetry) -> List[dict]:
        recognised = telemetry.gesture is not None and str(telemetry.gesture.value) != "NONE"
        confidence = (
            f"{telemetry.gesture_confidence * 100:.0f}%"
            if telemetry.gesture_confidence is not None else "—"
        )
        rows = [
            {
                "name": "Current gesture",
                "desc": f"Engine {telemetry.gesture_state.display_label.lower()}",
                "height": ROW_HEIGHT + 8,
                "value": (
                    (telemetry.gesture.label.capitalize(), COLOR_ACCENT) if recognised
                    else ("None", COLOR_TEXT_DIM)
                ),
            },
            {
                "name": "Confidence",
                "desc": "Geometric evidence for the held pose",
                "height": ROW_HEIGHT,
                "value": (confidence, COLOR_TEXT),
            },
        ]
        for name, desc, mode in (
            ("Point", "Move the pointer", "Mouse"),
            ("Pinch", "Click; hold to drag. Toggles mute in device mode", "Both"),
            ("Two fingers", "Scroll; volume in device mode", "Both"),
            ("Fist", "Brightness in device mode", "Device"),
            ("Open palm", "Emergency stop when held", "Both"),
            ("Swipe left / right", "Previous and next track", "Device"),
        ):
            rows.append({
                "name": name,
                "desc": desc,
                "height": ROW_HEIGHT,
                "value": (mode, COLOR_TEXT_DIM),
            })
        return rows

    def _safety_rows(self, telemetry: Telemetry) -> List[dict]:
        stopped = (
            telemetry.control_state is ControlState.EMERGENCY_STOP
            or telemetry.device_state is ControlState.EMERGENCY_STOP
        )
        gate_ok = not telemetry.control_suspended
        gate_desc = (
            "Actions suspend when tracking confidence falls below threshold"
            if gate_ok else (telemetry.control_message or "Actions are suspended")
        )
        return [
            {
                "name": "Emergency stop",
                "desc": "Stops mouse and device control and latches until recovery",
                "height": ROW_HEIGHT + 8,
                "value": (
                    ("Stopped", COLOR_DANGER) if stopped else ("Armed", COLOR_SUCCESS)
                ),
                "buttons": [
                    ("recover", "Recover", "success", "refresh", stopped)
                ] if stopped else [
                    ("emergency_stop", "Stop", "danger", "stop", True)
                ],
                "button_width": 104,
            },
            {
                "name": "Safety gate",
                "desc": gate_desc,
                "height": ROW_HEIGHT,
                "value": ("OK", COLOR_SUCCESS) if gate_ok
                else ("Suspended", COLOR_WARNING),
            },
            {
                "name": "Clicks",
                "desc": "Mouse clicks performed this session",
                "height": ROW_HEIGHT,
                "value": (str(telemetry.control_clicks), COLOR_TEXT),
            },
            {
                "name": "Device actions",
                "desc": "Device actions performed this session",
                "height": ROW_HEIGHT,
                "value": (str(telemetry.device_actions), COLOR_TEXT),
            },
            {
                "name": "Emergency stops",
                "desc": "Times the stop has tripped this session",
                "height": ROW_HEIGHT,
                "value": (
                    str(telemetry.control_emergency_stops + telemetry.device_emergency_stops),
                    COLOR_TEXT,
                ),
            },
        ]

    def _activity_rows(self, telemetry: Telemetry) -> List[dict]:
        count = len(telemetry.feedback)
        feedback_view = self._feedback_view

        def draw(surface, rect, telemetry_, fonts):
            body = rect.inflate(-12, -26)
            body.top = rect.top + 34
            feedback_view.render_activity(surface, body, telemetry_, fonts)

        return [{
            "name": "Recent actions",
            "desc": "Newest first, held in memory only",
            "height": ROW_HEIGHT + max(0, min(8, count)) * 22 + 10,
            "value": (str(count), COLOR_TEXT),
            "custom": draw,
        }]
