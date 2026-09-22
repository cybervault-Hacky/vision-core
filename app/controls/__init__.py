"""Touchless mouse control layer.

Separated from the gesture layer on purpose: recognition never touches an
operating system API, and the control layer never performs recognition.

    app.gestures.GestureEngine  ->  app.controls.MouseController
                                             |
                                             v
                                   PlatformMouseBackend  ->  OS cursor
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
from app.controls.mouse import MouseController
from app.controls.mouse_mapper import CursorMapper
from app.controls.safety import (
    ControlAction,
    ControlSettings,
    ControlSnapshot,
    ControlState,
    SafetyGate,
)

__all__ = [
    "ControlAction",
    "ControlSettings",
    "ControlSnapshot",
    "ControlState",
    "CursorMapper",
    "MouseButton",
    "MouseController",
    "PlatformMouseBackend",
    "SafetyGate",
    "ScreenGeometry",
    "UnsupportedMouseBackend",
    "WindowsMouseBackend",
    "X11MouseBackend",
    "create_backend",
]
