"""UI components, workspaces and animations for VisionCore."""

from ui.animations import ProgressAnimation, PulseAnimation
from ui.boot_screen import BootScreen
from ui.camera_view import CameraView
from ui.controls_page import ControlsPage
from ui.gesture_overlay import GestureOverlay
from ui.hand_overlay import HandOverlay
from ui.icons import icon
from ui.settings_page import SettingsPage
from ui.window import MainWindow, Page

__all__ = [
    "BootScreen",
    "CameraView",
    "ControlsPage",
    "GestureOverlay",
    "HandOverlay",
    "MainWindow",
    "Page",
    "ProgressAnimation",
    "PulseAnimation",
    "SettingsPage",
    "icon",
]
