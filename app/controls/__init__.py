"""Touchless control layers: mouse and device control.

Separated from the gesture layer on purpose: recognition never touches an
operating system API, and the control layers never perform recognition. Mouse and
device control stay independent of each other and are arbitrated by the control
mode (``MOUSE`` or ``DEVICE``), so one gesture can never trigger both.

    app.gestures.GestureEngine  ->  app.controls.MouseController
                                |            |
                                |            v
                                |   PlatformMouseBackend  ->  OS cursor
                                v
                            app.controls.DeviceController
                                         |
                                         v
                                DeviceBackend  ->  OS device APIs
"""

from app.controls.backend import (
    MouseButton,
    PlatformMouseBackend,
    ScreenGeometry,
    UnsupportedMouseBackend,
    WindowsMouseBackend,
    X11MouseBackend,
    create_backend,
)
from app.controls.device import DeviceController
from app.controls.device_backend import (
    DeviceBackend,
    LinuxDeviceBackend,
    UnsupportedDeviceBackend,
    WindowsDeviceBackend,
    create_device_backend,
)
from app.controls.device_types import (
    CapabilityReport,
    DeviceAction,
    DeviceActionResult,
    DeviceCapability,
    DeviceSettings,
    DeviceSnapshot,
    MediaAction,
    WindowAction,
)
from app.controls.launcher import ApplicationLauncher
from app.controls.mouse import MouseController
from app.controls.mouse_mapper import CursorMapper
from app.controls.safety import (
    ControlAction,
    ControlMode,
    ControlSettings,
    ControlSnapshot,
    ControlState,
    SafetyGate,
)

__all__ = [
    "ApplicationLauncher",
    "CapabilityReport",
    "ControlAction",
    "ControlMode",
    "ControlSettings",
    "ControlSnapshot",
    "ControlState",
    "CursorMapper",
    "DeviceAction",
    "DeviceActionResult",
    "DeviceBackend",
    "DeviceCapability",
    "DeviceController",
    "DeviceSettings",
    "DeviceSnapshot",
    "LinuxDeviceBackend",
    "MediaAction",
    "MouseButton",
    "MouseController",
    "PlatformMouseBackend",
    "SafetyGate",
    "ScreenGeometry",
    "UnsupportedDeviceBackend",
    "UnsupportedMouseBackend",
    "WindowAction",
    "WindowsDeviceBackend",
    "WindowsMouseBackend",
    "X11MouseBackend",
    "create_backend",
    "create_device_backend",
]
