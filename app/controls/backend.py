"""Platform mouse backends for VisionCore.

The controller never touches an operating system input API directly: it talks to
a :class:`PlatformMouseBackend`. Each backend is a thin, defensive wrapper around
the native interface of one platform, and every call returns ``True`` only when
the operating system accepted the request. A backend that cannot work reports
itself as unavailable with a human readable reason instead of failing silently.

Implemented platforms
---------------------
``WINDOWS``
    ``user32`` via ``ctypes``: absolute cursor positioning through ``SendInput``
    and wheel notches through ``MOUSEEVENTF_WHEEL``.
``LINUX_X11``
    ``libX11`` for absolute pointer warping and the XTEST extension
    (``libXtst``) for synthetic button and wheel events. A Wayland session
    without an X server is reported as unsupported rather than half working.
``UNSUPPORTED``
    Everything else. It accepts every call and does nothing, so the rest of the
    application stays safe and honest on platforms without an implementation.

No dependency is added: only the standard library ``ctypes`` is used.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.controls.x11 import X11Session

logger = logging.getLogger("visioncore.controls.backend")


class MouseButton(Enum):
    """Mouse buttons the controller is allowed to use."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"
    MIDDLE = "MIDDLE"


@dataclass(frozen=True, slots=True)
class ScreenGeometry:
    """Pixel geometry of the desktop available for cursor positioning."""

    x: int
    y: int
    width: int
    height: int

    @property
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0


class _MOUSEINPUT(ctypes.Structure):
    """``MOUSEINPUT`` member of the Win32 ``INPUT`` structure."""

    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class _INPUT(ctypes.Structure):
    """Single entry of the Win32 ``INPUT`` array used by ``SendInput``."""

    _fields_ = (("type", ctypes.c_ulong), ("mi", _MOUSEINPUT))


class PlatformMouseBackend:
    """Base class describing the small contract the controller relies on."""

    name = "UNSUPPORTED"

    def __init__(self) -> None:
        self.available = False
        # ``reason`` is short enough for a HUD row; ``detail`` carries the full
        # explanation and is only logged.
        self.reason = "PLATFORM NOT SUPPORTED"
        self.detail = "No mouse backend for this platform"

    # -- lifecycle --------------------------------------------------------- #

    def probe(self) -> bool:
        """Verify the backend can act on this host. Never raises."""
        return self.available

    def close(self) -> None:
        """Release any native handle. Safe to call more than once."""

    # -- capabilities ------------------------------------------------------ #

    @property
    def supports_buttons(self) -> bool:
        """True when this backend can synthesise button and wheel events."""
        return False

    def screen_geometry(self) -> Optional[ScreenGeometry]:
        """Pixel bounds of the desktop, or ``None`` when it cannot be read."""
        return None

    # -- actions ----------------------------------------------------------- #

    def move_absolute(self, x: int, y: int) -> bool:
        return False

    def button_down(self, button: MouseButton) -> bool:
        return False

    def button_up(self, button: MouseButton) -> bool:
        return False

    def scroll(self, notches: int) -> bool:
        """Positive notches scroll up, negative ones scroll down."""
        return False


class UnsupportedMouseBackend(PlatformMouseBackend):
    """Explicit no-op backend used when no implementation exists."""

    name = "UNSUPPORTED"

    def __init__(self, reason: str = "PLATFORM NOT SUPPORTED", detail: str = "") -> None:
        super().__init__()
        self.available = False
        self.reason = reason
        self.detail = detail or reason

    def probe(self) -> bool:
        logger.info("Mouse control unavailable: %s", self.detail)
        return False


class WindowsMouseBackend(PlatformMouseBackend):
    """Absolute cursor positioning and synthetic events through ``user32``."""

    name = "WINDOWS"

    # SendInput event flags.
    _INPUT_MOUSE = 0
    _MOVE = 0x0001
    _ABSOLUTE = 0x8000
    _VIRTUALDESK = 0x4000
    _LEFT_DOWN = 0x0002
    _LEFT_UP = 0x0004
    _RIGHT_DOWN = 0x0008
    _RIGHT_UP = 0x0010
    _MIDDLE_DOWN = 0x0020
    _MIDDLE_UP = 0x0040
    _WHEEL = 0x0800
    _WHEEL_DELTA = 120

    # Virtual screen metrics.
    _SM_XVIRTUALSCREEN = 76
    _SM_YVIRTUALSCREEN = 77
    _SM_CXVIRTUALSCREEN = 78
    _SM_CYVIRTUALSCREEN = 79

    def __init__(self) -> None:
        super().__init__()
        self._user32 = None

    def probe(self) -> bool:
        if sys.platform != "win32":
            self.reason = "WINDOWS BACKEND ON WRONG HOST"
            self.detail = "Windows backend loaded on a non Windows host"
            return False
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            # Best effort DPI awareness so the reported geometry matches the
            # coordinates the input API expects.
            try:
                ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
            except Exception:
                try:
                    self._user32.SetProcessDPIAware()
                except Exception:
                    pass
        except Exception as exc:  # pragma: no cover - platform dependent
            self.reason = "USER32 UNAVAILABLE"
            self.detail = f"user32 could not be loaded ({exc})"
            return False

        if not self.screen_geometry():
            self.reason = "DESKTOP GEOMETRY UNAVAILABLE"
            self.detail = "Could not read the virtual desktop metrics"
            return False

        self.available = True
        self.reason = "READY"
        self.detail = "SendInput based backend ready"
        return True

    @property
    def supports_buttons(self) -> bool:
        return self._user32 is not None

    def screen_geometry(self) -> Optional[ScreenGeometry]:
        if self._user32 is None:
            return None
        try:
            metrics = (
                self._SM_XVIRTUALSCREEN,
                self._SM_YVIRTUALSCREEN,
                self._SM_CXVIRTUALSCREEN,
                self._SM_CYVIRTUALSCREEN,
            )
            x, y, width, height = (self._user32.GetSystemMetrics(m) for m in metrics)
            geometry = ScreenGeometry(int(x), int(y), int(width), int(height))
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("Could not read Windows desktop geometry: %s", exc)
            return None
        return geometry if geometry.valid else None

    def _send(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> bool:
        if self._user32 is None:
            return False
        try:
            payload = _INPUT(
                type=self._INPUT_MOUSE,
                mi=_MOUSEINPUT(dx, dy, data, flags, 0, None),
            )
            sent = self._user32.SendInput(1, ctypes.byref(payload), ctypes.sizeof(payload))
            return bool(sent)
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("Windows input call failed: %s", exc)
            return False

    def move_absolute(self, x: int, y: int) -> bool:
        geometry = self.screen_geometry()
        if geometry is None:
            return False
        # SendInput expects the position normalised to 0..65535 of the virtual
        # desktop, so the mapping is independent of any resolution.
        nx = int(round((x - geometry.x) * 65535 / max(1, geometry.width - 1)))
        ny = int(round((y - geometry.y) * 65535 / max(1, geometry.height - 1)))
        flags = self._MOVE | self._ABSOLUTE | self._VIRTUALDESK
        return self._send(flags, max(0, min(65535, nx)), max(0, min(65535, ny)))

    def button_down(self, button: MouseButton) -> bool:
        flags = {
            MouseButton.LEFT: self._LEFT_DOWN,
            MouseButton.RIGHT: self._RIGHT_DOWN,
            MouseButton.MIDDLE: self._MIDDLE_DOWN,
        }[button]
        return self._send(flags)

    def button_up(self, button: MouseButton) -> bool:
        flags = {
            MouseButton.LEFT: self._LEFT_UP,
            MouseButton.RIGHT: self._RIGHT_UP,
            MouseButton.MIDDLE: self._MIDDLE_UP,
        }[button]
        return self._send(flags)

    def scroll(self, notches: int) -> bool:
        if notches == 0:
            return True
        return self._send(self._WHEEL, data=int(notches) * self._WHEEL_DELTA)


class X11MouseBackend(PlatformMouseBackend):
    """Pointer warping and button events through the shared X11 session."""

    name = "LINUX_X11"

    # X11 wheel buttons.
    _WHEEL_UP = 4
    _WHEEL_DOWN = 5

    def __init__(self) -> None:
        super().__init__()
        self._session = X11Session()

    def probe(self) -> bool:
        if not self._session.open():
            self.reason = self._session.reason
            self.detail = self._session.detail
            return False

        self.available = True
        self.reason = self._session.reason
        self.detail = self._session.detail
        return True

    @property
    def supports_buttons(self) -> bool:
        return self._session.keys_ready

    def screen_geometry(self) -> Optional[ScreenGeometry]:
        size = self._session.screen_size()
        if size is None:
            return None
        geometry = ScreenGeometry(0, 0, size[0], size[1])
        return geometry if geometry.valid else None

    def move_absolute(self, x: int, y: int) -> bool:
        return self._session.warp_pointer(x, y)

    def button_down(self, button: MouseButton) -> bool:
        return self._session.fake_button(1 if button is MouseButton.LEFT else 2, True)

    def button_up(self, button: MouseButton) -> bool:
        return self._session.fake_button(1 if button is MouseButton.LEFT else 2, False)

    def scroll(self, notches: int) -> bool:
        if notches == 0:
            return True
        button = self._WHEEL_UP if notches > 0 else self._WHEEL_DOWN
        ok = True
        for _ in range(min(abs(int(notches)), 8)):
            ok = self._session.fake_button(button, True) and ok
            ok = self._session.fake_button(button, False) and ok
        return ok

    def close(self) -> None:
        self._session.close()


def create_backend() -> PlatformMouseBackend:
    """Create and probe the backend for the current platform."""
    if sys.platform.startswith("win"):
        backend: PlatformMouseBackend = WindowsMouseBackend()
    elif sys.platform.startswith("linux"):
        backend = X11MouseBackend()
    else:
        backend = UnsupportedMouseBackend(
            "PLATFORM NOT SUPPORTED", f"Platform '{sys.platform}' has no mouse backend"
        )

    if isinstance(backend, UnsupportedMouseBackend):
        backend.probe()
    elif not backend.probe():
        logger.info(
            "Mouse control backend '%s' unavailable: %s", backend.name, backend.detail
        )
    else:
        logger.info("Mouse control backend '%s' ready", backend.name)
    return backend
