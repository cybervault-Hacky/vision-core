"""Platform detection and environment diagnostics for VisionCore."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PlatformInfo:
    """Encapsulates system platform attributes and capabilities."""

    system: str
    release: str
    machine: str
    python_version: str
    python_compiler: str
    display_server: Optional[str]
    is_linux: bool
    is_windows: bool
    is_macos: bool
    is_headless: bool

    @classmethod
    def current(cls) -> PlatformInfo:
        """Inspect and return the current system platform details."""
        sys_name = platform.system()
        is_linux = sys_name == "Linux"
        is_windows = sys_name == "Windows"
        is_macos = sys_name == "Darwin"

        display_server: Optional[str] = None
        is_headless = False

        if is_linux:
            if "WAYLAND_DISPLAY" in os.environ:
                display_server = "Wayland"
            elif "DISPLAY" in os.environ and os.environ["DISPLAY"].strip():
                display_server = f"X11 ({os.environ['DISPLAY']})"
            else:
                display_server = "None (Headless)"
                is_headless = True
        elif is_windows:
            display_server = "Win32 GDI/DirectX"
        elif is_macos:
            display_server = "macOS Quartz/Metal"
        else:
            display_server = sys_name

        return cls(
            system=sys_name or "Unknown",
            release=platform.release(),
            machine=platform.machine(),
            python_version=platform.python_version(),
            python_compiler=platform.python_compiler(),
            display_server=display_server,
            is_linux=is_linux,
            is_windows=is_windows,
            is_macos=is_macos,
            is_headless=is_headless,
        )

    def camera_permission_hint(self) -> str:
        """Provide platform-specific troubleshooting advice for camera access."""
        if self.is_macos:
            return (
                "Ensure Terminal or Python has camera access enabled in "
                "System Settings -> Privacy & Security -> Camera."
            )
        if self.is_windows:
            return (
                "Verify camera permissions under Windows Settings -> Privacy & Security -> "
                "Camera -> 'Let desktop apps access your camera'."
            )
        if self.is_linux:
            return (
                "Ensure your user account is in the 'video' group (e.g. sudo usermod -aG video $USER) "
                "and that /dev/video0 exists."
            )
        return "Check that your camera hardware is connected and permissions are granted."
