"""The Settings workspace: the application's real configuration and state.

Settings are presented as clean rows - a name, a short explanation and the
current value - grouped into General, Camera, Tracking, AI, Voice, Controls,
Safety and About.

The page is deliberately honest about what it is: it shows the configuration
the application actually loaded and the values the subsystems actually
measured. The only interactive controls are the two toggles that already exist
as runtime mechanisms (the diagnostics overlay and fullscreen); everything else
is read from ``config.json`` and the environment, which remain the way to
change it. No setting here is decorative, and no secret is ever displayed.
"""

from __future__ import annotations

from typing import List, Tuple

from app.config import AppConfig
from app.state import Telemetry
from ui.section_page import SectionPage
from ui.theme import (
    COLOR_DISABLED,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_WARNING,
    ai_status_color,
)

ROW_HEIGHT = 44
LICENSE = "MIT"


class SettingsPage(SectionPage):
    """The application's settings, read from the real configuration."""

    title = "Settings"

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.window_size: Tuple[int, int] = (config.window_width, config.window_height)

    # -- sections ---------------------------------------------------------- #

    def _build_sections(self, telemetry: Telemetry, fonts) -> List[Tuple[str, str, List[dict]]]:
        return [
            ("General", "gear", self._general_rows(telemetry)),
            ("Camera", "camera", self._camera_rows(telemetry)),
            ("Tracking", "eye", self._tracking_rows(telemetry)),
            ("AI", "spark", self._ai_rows(telemetry)),
            ("Voice", "mic", self._voice_rows(telemetry)),
            ("Controls", "sliders", self._control_rows()),
            ("Safety", "shield", self._safety_rows(telemetry)),
            ("About", "info", self._about_rows()),
        ]

    def _general_rows(self, telemetry: Telemetry) -> List[dict]:
        fps = f"{telemetry.render_fps:.1f} FPS" if telemetry.render_fps > 0 else "—"
        return [
            {
                "name": "Version",
                "desc": "Current release",
                "height": ROW_HEIGHT,
                "value": ("v1.0.0", COLOR_TEXT),
            },
            {
                "name": "Uptime",
                "desc": "Time since the application started",
                "height": ROW_HEIGHT,
                "value": (telemetry.formatted_uptime, COLOR_TEXT),
            },
            {
                "name": "Performance",
                "desc": "Measured interface frame rate",
                "height": ROW_HEIGHT,
                "value": (fps, COLOR_TEXT),
            },
            {
                "name": "Window",
                "desc": "Current window size",
                "height": ROW_HEIGHT,
                "value": (f"{self.window_size[0]} × {self.window_size[1]}", COLOR_TEXT),
            },
            {
                "name": "Diagnostics overlay",
                "desc": "Show measured latencies on the camera",
                "height": ROW_HEIGHT + 4,
                "switch": ("toggle_diagnostics", telemetry.diagnostics_visible, True),
            },
            {
                "name": "Fullscreen",
                "desc": "Use the whole display (F11)",
                "height": ROW_HEIGHT + 4,
                "switch": ("toggle_fullscreen", self.config.fullscreen, True),
            },
        ]

    def _camera_rows(self, telemetry: Telemetry) -> List[dict]:
        resolution = (
            f"{telemetry.camera_width} × {telemetry.camera_height}"
            if telemetry.camera_width > 0
            else f"{self.config.camera_width} × {self.config.camera_height} (configured)"
        )
        return [
            {
                "name": "Device",
                "desc": "Camera index used at start-up",
                "height": ROW_HEIGHT,
                "value": (f"Camera {self.config.camera_index}", COLOR_TEXT),
            },
            {
                "name": "Resolution",
                "desc": "Capture size reported by the camera",
                "height": ROW_HEIGHT,
                "value": (resolution, COLOR_TEXT),
            },
            {
                "name": "Target frame rate",
                "desc": "Requested capture rate",
                "height": ROW_HEIGHT,
                "value": (f"{self.config.target_fps} FPS", COLOR_TEXT),
            },
            {
                "name": "Mirrored",
                "desc": "Flip the feed horizontally for a natural mirror",
                "height": ROW_HEIGHT,
                "value": ("On" if self.config.mirror_camera else "Off", COLOR_TEXT),
            },
            {
                "name": "Backend",
                "desc": "Capture backend in use",
                "height": ROW_HEIGHT,
                "value": (telemetry.camera_backend, COLOR_TEXT_DIM),
            },
            {
                "name": "Frames received",
                "desc": "Frames read this session",
                "height": ROW_HEIGHT,
                "value": (f"{telemetry.frame_count:,}", COLOR_TEXT),
            },
        ]

    def _tracking_rows(self, telemetry: Telemetry) -> List[dict]:
        configured = self.config.tracking_enabled
        rate = (
            f"{telemetry.tracker_fps:.1f} Hz" if telemetry.tracker_fps > 0 else "—"
        )
        latency = (
            f"{telemetry.tracking_latency_ms:.1f} ms"
            if telemetry.tracking_latency_ms > 0 else "—"
        )
        return [
            {
                "name": "Hand tracking",
                "desc": "On-device landmark inference",
                "height": ROW_HEIGHT,
                "value": (
                    ("Enabled", COLOR_SUCCESS) if configured
                    else ("Disabled", COLOR_DISABLED)
                ),
            },
            {
                "name": "Engine",
                "desc": "Inference engine reported by the tracker",
                "height": ROW_HEIGHT,
                "value": (
                    (telemetry.tracking_engine.capitalize(), COLOR_TEXT_DIM)
                    if telemetry.tracking_engine
                    else ("—", COLOR_TEXT_DIM)
                ),
            },
            {
                "name": "Maximum hands",
                "desc": "Hands tracked simultaneously",
                "height": ROW_HEIGHT,
                "value": (str(self.config.max_hands), COLOR_TEXT),
            },
            {
                "name": "Model complexity",
                "desc": "Lite favours speed, full favours accuracy",
                "height": ROW_HEIGHT,
                "value": (
                    "Lite" if self.config.tracking_model_complexity == 0 else "Full",
                    COLOR_TEXT,
                ),
            },
            {
                "name": "Detection confidence",
                "desc": "Minimum confidence to accept a hand",
                "height": ROW_HEIGHT,
                "value": (f"{self.config.min_detection_confidence:.2f}", COLOR_TEXT),
            },
            {
                "name": "Tracking confidence",
                "desc": "Minimum confidence to keep following a hand",
                "height": ROW_HEIGHT,
                "value": (f"{self.config.min_tracking_confidence:.2f}", COLOR_TEXT),
            },
            {
                "name": "Pipeline rate",
                "desc": "Measured tracker throughput",
                "height": ROW_HEIGHT,
                "value": (rate, COLOR_TEXT),
            },
            {
                "name": "Inference latency",
                "desc": "Measured per-frame inference cost",
                "height": ROW_HEIGHT,
                "value": (latency, COLOR_TEXT),
            },
        ]

    def _ai_rows(self, telemetry: Telemetry) -> List[dict]:
        snapshot = telemetry.ai
        provider = snapshot.provider_label if snapshot.configured else "None"
        model = snapshot.model if snapshot.model else "—"
        status_color = ai_status_color(snapshot.status)
        return [
            {
                "name": "Provider",
                "desc": "Configured through VISIONCORE_AI_PROVIDER and "
                        "VISIONCORE_AI_API_KEY",
                "height": ROW_HEIGHT,
                "value": (provider, COLOR_TEXT),
            },
            {
                "name": "Model",
                "desc": "Model reported by the provider",
                "height": ROW_HEIGHT,
                "value": (model, COLOR_TEXT_DIM),
            },
            {
                "name": "Status",
                "desc": "The assistant's real state right now",
                "height": ROW_HEIGHT,
                "value": (snapshot.status_label, status_color),
            },
            {
                "name": "Conversation turns",
                "desc": "Held in memory only, never written to disk",
                "height": ROW_HEIGHT,
                "value": (str(snapshot.turns), COLOR_TEXT),
            },
            {
                "name": "Local answers",
                "desc": "Without a provider, questions about VisionCore's own "
                        "state are answered deterministically on this machine",
                "height": ROW_HEIGHT + 4,
                "value": ("Always available", COLOR_TEXT_DIM),
            },
        ]

    def _voice_rows(self, telemetry: Telemetry) -> List[dict]:
        voice = telemetry.voice
        availability_desc = (
            "An engine and microphone are available"
            if voice.available
            else (voice.note or voice.detail or "No local speech engine or microphone")
        )
        limit = f"{voice.limit_seconds:.0f} s" if voice.limit_seconds else "—"
        return [
            {
                "name": "Engine",
                "desc": "Local speech recognition engine",
                "height": ROW_HEIGHT,
                "value": (voice.engine.capitalize() if voice.engine else "—", COLOR_TEXT_DIM),
            },
            {
                "name": "State",
                "desc": "The microphone's real state right now",
                "height": ROW_HEIGHT,
                "value": (voice.status_label, COLOR_TEXT_DIM),
            },
            {
                "name": "Availability",
                "desc": availability_desc,
                "height": ROW_HEIGHT + 4,
                "value": (
                    ("Available", COLOR_SUCCESS) if voice.available
                    else ("Unavailable", COLOR_DISABLED)
                ),
            },
            {
                "name": "Listening window",
                "desc": "Maximum time the microphone stays open per activation",
                "height": ROW_HEIGHT,
                "value": (limit, COLOR_TEXT),
            },
            {
                "name": "Transcripts",
                "desc": "Voice transcripts converted to messages this session",
                "height": ROW_HEIGHT,
                "value": (str(voice.transcripts), COLOR_TEXT),
            },
        ]

    def _control_rows(self) -> List[dict]:
        config = self.config
        return [
            {
                "name": "Cursor smoothing",
                "desc": "Damping applied to pointer movement",
                "height": ROW_HEIGHT,
                "value": (f"{config.cursor_smoothing:.2f}", COLOR_TEXT),
            },
            {
                "name": "Cursor speed",
                "desc": "Pointer gain",
                "height": ROW_HEIGHT,
                "value": (f"{config.cursor_speed:.2f}", COLOR_TEXT),
            },
            {
                "name": "Click cooldown",
                "desc": "Minimum gap between two clicks",
                "height": ROW_HEIGHT,
                "value": (f"{config.click_cooldown:.2f} s", COLOR_TEXT),
            },
            {
                "name": "Drag hold",
                "desc": "Pinch hold that turns a click into a drag",
                "height": ROW_HEIGHT,
                "value": (f"{config.drag_hold_sec:.2f} s", COLOR_TEXT),
            },
            {
                "name": "Scroll sensitivity",
                "desc": "Wheel notches per unit of travel",
                "height": ROW_HEIGHT,
                "value": (f"{config.scroll_sensitivity:.1f}", COLOR_TEXT),
            },
            {
                "name": "Volume sensitivity",
                "desc": "Volume change per unit of vertical travel",
                "height": ROW_HEIGHT,
                "value": (f"{config.volume_sensitivity:.0f}", COLOR_TEXT),
            },
            {
                "name": "Brightness sensitivity",
                "desc": "Brightness change per unit of vertical travel",
                "height": ROW_HEIGHT,
                "value": (f"{config.brightness_sensitivity:.0f}", COLOR_TEXT),
            },
            {
                "name": "Device action cooldown",
                "desc": "Minimum gap between event actions",
                "height": ROW_HEIGHT,
                "value": (f"{config.device_action_cooldown:.2f} s", COLOR_TEXT),
            },
        ]

    def _safety_rows(self, telemetry: Telemetry) -> List[dict]:
        launchable = ", ".join(telemetry.device_launchable.keys())
        return [
            {
                "name": "Confidence threshold",
                "desc": "Actions suspend below this tracking confidence",
                "height": ROW_HEIGHT,
                "value": (f"{self.config.safety_confidence_threshold:.2f}", COLOR_WARNING),
            },
            {
                "name": "Emergency stop hold",
                "desc": "Open palm hold that trips the mouse-layer stop",
                "height": ROW_HEIGHT,
                "value": (f"{self.config.emergency_stop_sec:.2f} s", COLOR_TEXT),
            },
            {
                "name": "Device stop hold",
                "desc": "Open palm hold that trips the device-layer stop",
                "height": ROW_HEIGHT,
                "value": (f"{self.config.device_emergency_stop_sec:.2f} s", COLOR_TEXT),
            },
            {
                "name": "Allowlisted applications",
                "desc": "Only these may be launched by the launcher",
                "height": ROW_HEIGHT + 4,
                "value": (
                    (launchable, COLOR_TEXT_DIM) if launchable
                    else ("None on this host", COLOR_DISABLED)
                ),
            },
        ]

    def _about_rows(self) -> List[dict]:
        shortcuts = (
            ("1 – 4", "Switch workspace"),
            ("A", "AI workspace"),
            ("V", "Microphone"),
            ("C", "Pause or resume control"),
            ("M / D", "Mouse or device mode"),
            ("P", "Diagnostics overlay"),
            ("R", "Retry camera"),
            ("F11", "Fullscreen"),
            ("Esc", "Shut down"),
        )
        rows = [
            {
                "name": "VisionCore",
                "desc": "Local-first hand tracking and touchless computer control",
                "height": ROW_HEIGHT + 4,
                "value": ("v1.0.0", COLOR_TEXT),
            },
            {
                "name": "Privacy",
                "desc": "Camera frames and landmarks stay in memory. Voice is "
                        "optional, local-only and off by default. AI is disabled "
                        "until a provider is configured.",
                "height": ROW_HEIGHT + 4,
                "value": ("Local-first", COLOR_SUCCESS),
            },
            {
                "name": "License",
                "desc": "Released under the MIT license",
                "height": ROW_HEIGHT,
                "value": (LICENSE, COLOR_TEXT_DIM),
            },
        ]
        rows.extend({
            "name": key,
            "desc": action,
            "height": 44,
        } for key, action in shortcuts)
        return rows
