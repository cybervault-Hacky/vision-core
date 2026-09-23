"""Typed vocabulary for touchless device control.

Everything the device layer exchanges is typed: actions, capabilities, results
and the immutable snapshot the interface reads. No action is ever identified by a
free-form string, and no capability is assumed - a platform reports exactly what
it can do and the interface displays that report.

The settings object carries every tunable, is clamped on construction, and the
controller treats ``enabled=False`` as "this feature does not exist", never as a
silent no-op that pretends to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Tuple


class DeviceCapability(Enum):
    """A device feature the platform may or may not provide."""

    VOLUME = "VOLUME"
    MEDIA = "MEDIA"
    BRIGHTNESS = "BRIGHTNESS"
    WINDOW = "WINDOW"
    LAUNCHER = "LAUNCHER"

    @property
    def label(self) -> str:
        return self.value


class MediaAction(Enum):
    """Transport controls handed to the platform media session."""

    PLAY_PAUSE = "PLAY_PAUSE"
    NEXT = "NEXT_TRACK"
    PREVIOUS = "PREVIOUS_TRACK"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


class WindowAction(Enum):
    """Safe window operations on the currently active window."""

    MINIMIZE = "MINIMIZE"
    MAXIMIZE = "MAXIMIZE"
    NEXT = "NEXT_WINDOW"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


class DeviceAction(Enum):
    """Every device action this phase can perform."""

    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    MUTE = "MUTE"
    PLAY_PAUSE = "PLAY_PAUSE"
    NEXT_TRACK = "NEXT_TRACK"
    PREVIOUS_TRACK = "PREVIOUS_TRACK"
    BRIGHTNESS_UP = "BRIGHTNESS_UP"
    BRIGHTNESS_DOWN = "BRIGHTNESS_DOWN"
    MINIMIZE = "MINIMIZE"
    MAXIMIZE = "MAXIMIZE"
    NEXT_WINDOW = "NEXT_WINDOW"
    LAUNCH_APP = "LAUNCH_APP"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")

    @property
    def is_continuous(self) -> bool:
        """True for actions driven by sustained movement (rate limited)."""
        return self in (
            DeviceAction.VOLUME_UP,
            DeviceAction.VOLUME_DOWN,
            DeviceAction.BRIGHTNESS_UP,
            DeviceAction.BRIGHTNESS_DOWN,
        )

    @property
    def capability(self) -> DeviceCapability:
        if self in (DeviceAction.VOLUME_UP, DeviceAction.VOLUME_DOWN, DeviceAction.MUTE):
            return DeviceCapability.VOLUME
        if self in (
            DeviceAction.PLAY_PAUSE,
            DeviceAction.NEXT_TRACK,
            DeviceAction.PREVIOUS_TRACK,
        ):
            return DeviceCapability.MEDIA
        if self in (DeviceAction.BRIGHTNESS_UP, DeviceAction.BRIGHTNESS_DOWN):
            return DeviceCapability.BRIGHTNESS
        if self in (DeviceAction.MINIMIZE, DeviceAction.MAXIMIZE, DeviceAction.NEXT_WINDOW):
            return DeviceCapability.WINDOW
        return DeviceCapability.LAUNCHER


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """What a platform can actually do, with the reason when it cannot."""

    capability: DeviceCapability
    available: bool
    detail: str = ""

    @property
    def status_label(self) -> str:
        if self.available:
            return "READY"
        return "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DeviceActionResult:
    """Structured outcome of one device action."""

    success: bool
    action: Optional[DeviceAction]
    message: str
    timestamp: float
    capability: Optional[DeviceCapability] = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DeviceSettings:
    """Tunable device control parameters (clamped on construction)."""

    enabled: bool = True                  # feature available at all
    volume_sensitivity: float = 55.0      # percent per full frame height
    volume_deadzone: float = 0.01         # normalised travel before acting
    volume_max_step: float = 8.0          # percent applied per action
    volume_interval_sec: float = 0.07     # rate limit between volume actions
    brightness_sensitivity: float = 60.0  # percent per full frame height
    brightness_deadzone: float = 0.01
    brightness_max_step: float = 10.0
    brightness_interval_sec: float = 0.09
    action_cooldown_sec: float = 0.80     # minimum gap between event actions
    emergency_stop_sec: float = 1.80      # open palm hold that stops everything
    level_refresh_sec: float = 2.0        # how often the OS level is re-read
    pinch_mutes: bool = True              # PINCH toggles mute in device mode

    def clamped(self) -> "DeviceSettings":
        return DeviceSettings(
            enabled=bool(self.enabled),
            volume_sensitivity=_clamp(self.volume_sensitivity, 5.0, 250.0),
            volume_deadzone=_clamp(self.volume_deadzone, 0.0, 0.20),
            volume_max_step=_clamp(self.volume_max_step, 1.0, 25.0),
            volume_interval_sec=_clamp(self.volume_interval_sec, 0.02, 1.0),
            brightness_sensitivity=_clamp(self.brightness_sensitivity, 5.0, 250.0),
            brightness_deadzone=_clamp(self.brightness_deadzone, 0.0, 0.20),
            brightness_max_step=_clamp(self.brightness_max_step, 1.0, 25.0),
            brightness_interval_sec=_clamp(self.brightness_interval_sec, 0.02, 1.0),
            action_cooldown_sec=_clamp(self.action_cooldown_sec, 0.15, 5.0),
            emergency_stop_sec=_clamp(self.emergency_stop_sec, 0.40, 8.0),
            level_refresh_sec=_clamp(self.level_refresh_sec, 0.20, 30.0),
            pinch_mutes=bool(self.pinch_mutes),
        )


def _clamp(value: float, low: float, high: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return low
    if numeric != numeric:  # NaN
        return low
    return max(low, min(high, numeric))


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """Immutable view of the device control layer for the HUD and telemetry."""

    state_label: str = "DISABLED"
    mode_label: str = "MOUSE"
    enabled: bool = False
    active: bool = False
    backend: str = "UNSUPPORTED"
    message: str = ""
    suspended: bool = False
    suspended_reason: str = ""
    capability_summary: Tuple[Tuple[str, bool], ...] = ()
    capability_details: Tuple[Tuple[str, str], ...] = ()   # (label, reason) pairs
    volume: Optional[float] = None          # 0..1 when the platform reports it
    volume_known: bool = False
    volume_steps: int = 0                   # relative changes when level is unknown
    muted: Optional[bool] = None
    brightness: Optional[float] = None
    brightness_known: bool = False
    action_label: str = ""
    action_success: bool = True
    action_detail: str = ""                 # platform reason or resolved app label
    action_age: float = 0.0
    actions_performed: int = 0
    # Monotonic count of user visible results (successes and refusals), so the
    # interface can report each one exactly once even when the label repeats.
    action_events: int = 0
    emergency_stops: int = 0
    launchable: Tuple[Tuple[str, str], ...] = ()   # (key, label) pairs

    @property
    def capability_map(self) -> Mapping[str, bool]:
        return dict(self.capability_summary)

    @property
    def capability_notes(self) -> Mapping[str, str]:
        """Why a capability is unavailable, as reported by the backend."""
        return dict(self.capability_details)
