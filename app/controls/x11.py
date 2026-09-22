"""Minimal X11 client shared by the Linux mouse and device backends.

The Linux control layer needs four low level capabilities:

* warp the pointer and synthesise button events (mouse control),
* synthesise key events for the media and volume keys (`XF86Audio*`),
* query and drive the window manager through EWMH for window actions,
* report the display size for cursor mapping.

Rather than duplicate ``ctypes`` plumbing in two backends, this module owns the
single X11 connection and exposes it as small, defensive methods. Every method
returns ``False``/``None`` instead of raising, so a missing extension or an
unexpected server response degrades into "action not performed" rather than a
crash. No dependency is added: only the standard library ``ctypes`` is used.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger("visioncore.controls.x11")

# X11 event and mask constants.
_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
_SUBSTRUCTURE_REDIRECT_MASK = 1 << 20
_ANY_PROPERTY_TYPE = 0
_MAX_CLIENT_WINDOWS = 256

# EWMH window state actions (see ICCCM/EWMH).
_NET_WM_STATE_REMOVE = 0
_NET_WM_STATE_ADD = 1
_NET_WM_STATE_TOGGLE = 2


class _ClientMessageData(ctypes.Union):
    _fields_ = (
        ("b", ctypes.c_char * 20),
        ("s", ctypes.c_short * 10),
        ("l", ctypes.c_long * 5),
    )


class _ClientMessageEvent(ctypes.Structure):
    _fields_ = (
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", _ClientMessageData),
    )


class _XEvent(ctypes.Union):
    """Union shaped like Xlib's XEvent (large enough for a client message)."""

    _fields_ = (
        ("type", ctypes.c_int),
        ("xclient", _ClientMessageEvent),
        ("pad", ctypes.c_long * 24),
    )


class X11Session:
    """A single, lazily opened X11 connection with the calls control needs."""

    name = "X11"

    def __init__(self) -> None:
        self.available = False
        self.reason = "X11 UNAVAILABLE"
        self.detail = "X11 session was not initialised"
        self.pointer_ready = False
        self.keys_ready = False
        self.window_ready = False
        self._xlib = None
        self._xtst = None
        self._display = None
        self._screen = 0
        self._root = 0
        self._atoms: dict[str, int] = {}

    # -- lifecycle --------------------------------------------------------- #

    def open(self) -> bool:
        """Open the display and bind the calls used by the control layer."""
        if sys.platform != "linux":
            self.reason = "X11 ON WRONG HOST"
            self.detail = "X11 session requested on a non Linux host"
            return False

        display_name = os.environ.get("DISPLAY")
        if not display_name:
            self.reason = "NO X DISPLAY SERVER"
            self.detail = (
                "DISPLAY is not set; a Wayland session without an X server is not "
                "supported for device control"
            )
            return False

        try:
            x11_path = ctypes.util.find_library("X11")
            if not x11_path:
                self.reason = "LIBX11 NOT INSTALLED"
                self.detail = "libX11 could not be located"
                return False
            xlib = ctypes.CDLL(x11_path)
            self._bind_xlib(xlib)
            display = xlib.XOpenDisplay(display_name.encode("utf-8"))
            if not display:
                self.reason = "X DISPLAY UNAVAILABLE"
                self.detail = f"Could not open X display '{display_name}'"
                return False
            self._xlib = xlib
            self._display = ctypes.c_void_p(display)
            self._screen = int(xlib.XDefaultScreen(self._display))
            self._root = int(xlib.XDefaultRootWindow(self._display))
        except Exception as exc:  # pragma: no cover - platform dependent
            self.reason = "X11 INITIALISATION FAILED"
            self.detail = f"X11 initialisation failed ({exc})"
            return False

        self.pointer_ready = True
        self.window_ready = True

        if not self._open_xtst():
            self.window_ready = False

        if self.screen_size() is None:
            self.reason = "X DISPLAY SIZE UNAVAILABLE"
            self.detail = "Could not read the X display dimensions"
            return False

        self.available = True
        self.reason = "READY" if self.keys_ready else "XTEST EXTENSION MISSING"
        self.detail = (
            "X11 pointer, key and window control ready"
            if self.keys_ready
            else "Pointer control available, synthetic keys and window focus unavailable"
        )
        return True

    @staticmethod
    def _bind_xlib(xlib) -> None:
        """Declare argument types so ctypes does not guess (important on 64 bit)."""
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XDefaultScreen.restype = ctypes.c_int
        xlib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDisplayWidth.restype = ctypes.c_int
        xlib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xlib.XDisplayHeight.restype = ctypes.c_int
        xlib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XFlush.argtypes = [ctypes.c_void_p]
        xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xlib.XWarpPointer.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
        ]
        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XStringToKeysym.restype = ctypes.c_ulong
        xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
        xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XIconifyWindow.restype = ctypes.c_int
        xlib.XIconifyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        xlib.XSendEvent.restype = ctypes.c_int
        xlib.XSendEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
            ctypes.POINTER(_XEvent),
        ]
        xlib.XGetWindowProperty.restype = ctypes.c_int
        xlib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        xlib.XFree.argtypes = [ctypes.c_void_p]

    def _open_xtst(self) -> bool:
        """Bind the XTEST extension; without it no synthetic event is possible."""
        try:
            xtst_path = ctypes.util.find_library("Xtst")
            if not xtst_path:
                self.reason = "XTEST EXTENSION MISSING"
                self.detail = "libXtst could not be located; clicks, keys and scrolling are unavailable"
                return False
            xtst = ctypes.CDLL(xtst_path)
            xtst.XTestFakeButtonEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
            ]
            xtst.XTestFakeKeyEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
            ]
            self._xtst = xtst
            self.keys_ready = True
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            self.reason = "XTEST EXTENSION MISSING"
            self.detail = f"XTEST extension unavailable ({exc})"
            return False

    def close(self) -> None:
        if self._xlib is not None and self._display is not None:
            try:
                self._xlib.XCloseDisplay(self._display)
            except Exception:
                pass
        self._display = None
        self._xlib = None
        self._xtst = None
        self.available = False
        self.pointer_ready = False
        self.keys_ready = False
        self.window_ready = False
        self._atoms.clear()

    # -- geometry ---------------------------------------------------------- #

    def screen_size(self) -> Optional[Tuple[int, int]]:
        if self._xlib is None or self._display is None:
            return None
        try:
            width = int(self._xlib.XDisplayWidth(self._display, self._screen))
            height = int(self._xlib.XDisplayHeight(self._display, self._screen))
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("Could not read X display size: %s", exc)
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

    # -- pointer and buttons ----------------------------------------------- #

    def warp_pointer(self, x: int, y: int) -> bool:
        if not self.pointer_ready or self._xlib is None:
            return False
        try:
            self._xlib.XWarpPointer(self._display, 0, self._root, 0, 0, 0, 0, int(x), int(y))
            self._xlib.XFlush(self._display)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("X11 pointer move failed: %s", exc)
            return False

    def fake_button(self, button: int, pressed: bool) -> bool:
        if not self.keys_ready or self._xtst is None:
            return False
        try:
            self._xtst.XTestFakeButtonEvent(
                self._display, int(button), 1 if pressed else 0, 0
            )
            self._xlib.XFlush(self._display)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("X11 button event failed: %s", exc)
            return False

    # -- keys -------------------------------------------------------------- #

    def keycode(self, keysym_name: str) -> int:
        if self._xlib is None or self._display is None:
            return 0
        try:
            keysym = self._xlib.XStringToKeysym(keysym_name.encode("utf-8"))
            if not keysym:
                return 0
            return int(self._xlib.XKeysymToKeycode(self._display, keysym))
        except Exception:  # pragma: no cover - platform dependent
            return 0

    def fake_key(self, keysym_name: str, pressed: bool) -> bool:
        if not self.keys_ready or self._xtst is None:
            return False
        code = self.keycode(keysym_name)
        if code == 0:
            logger.debug("No X keycode for %s", keysym_name)
            return False
        try:
            self._xtst.XTestFakeKeyEvent(self._display, code, 1 if pressed else 0, 0)
            self._xlib.XFlush(self._display)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("X11 key event failed: %s", exc)
            return False

    def tap_keys(self, keysym_names: Sequence[str]) -> bool:
        """Press a key combination in order, then release it in reverse."""
        if not self.keys_ready:
            return False
        codes = [self.keycode(name) for name in keysym_names]
        if not codes or any(code == 0 for code in codes):
            return False
        pressed: List[int] = []
        try:
            for code in codes:
                self._xtst.XTestFakeKeyEvent(self._display, code, 1, 0)
                pressed.append(code)
            for code in reversed(pressed):
                self._xtst.XTestFakeKeyEvent(self._display, code, 0, 0)
            self._xlib.XFlush(self._display)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("X11 key combination failed: %s", exc)
            return False

    # -- window manager (EWMH) --------------------------------------------- #

    def _atom(self, name: str) -> int:
        atom = self._atoms.get(name)
        if atom:
            return atom
        try:
            atom = int(self._xlib.XInternAtom(self._display, name.encode("utf-8"), 0))
        except Exception:  # pragma: no cover - platform dependent
            return 0
        self._atoms[name] = atom
        return atom

    def _window_list(self, property_name: str) -> List[int]:
        """Read a window-list property (for example ``_NET_CLIENT_LIST``)."""
        if self._xlib is None or self._display is None:
            return []
        atom = self._atom(property_name)
        if not atom:
            return []

        actual_type = ctypes.c_ulong(0)
        actual_format = ctypes.c_int(0)
        nitems = ctypes.c_ulong(0)
        bytes_after = ctypes.c_ulong(0)
        prop = ctypes.POINTER(ctypes.c_ubyte)()
        try:
            status = self._xlib.XGetWindowProperty(
                self._display, self._root, atom, 0, _MAX_CLIENT_WINDOWS, 0,
                _ANY_PROPERTY_TYPE, ctypes.byref(actual_type),
                ctypes.byref(actual_format), ctypes.byref(nitems),
                ctypes.byref(bytes_after), ctypes.byref(prop),
            )
            if status != 0 or not prop:
                return []
            count = min(int(nitems.value), _MAX_CLIENT_WINDOWS)
            values = ctypes.cast(prop, ctypes.POINTER(ctypes.c_ulong))
            return [int(values[index]) for index in range(count)]
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.debug("Could not read %s: %s", property_name, exc)
            return []
        finally:
            if prop:
                try:
                    self._xlib.XFree(prop)
                except Exception:
                    pass

    def active_window(self) -> Optional[int]:
        windows = self._window_list("_NET_ACTIVE_WINDOW")
        if not windows:
            return None
        return windows[0] or None

    def _send_client_message(
        self, target: int, message_name: str, values: Sequence[int]
    ) -> bool:
        if self._xlib is None or self._display is None:
            return False
        message_type = self._atom(message_name)
        if not message_type:
            return False
        try:
            event = _XEvent()
            event.xclient.type = _CLIENT_MESSAGE
            event.xclient.serial = 0
            event.xclient.send_event = 1
            event.xclient.display = self._display
            event.xclient.window = int(target)
            event.xclient.message_type = message_type
            event.xclient.format = 32
            data = event.xclient.data.l
            for index in range(min(5, len(values))):
                data[index] = int(values[index])
            mask = _SUBSTRUCTURE_REDIRECT_MASK | _SUBSTRUCTURE_NOTIFY_MASK
            self._xlib.XSendEvent(self._display, self._root, 0, mask, ctypes.byref(event))
            self._xlib.XFlush(self._display)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("X11 client message failed: %s", exc)
            return False

    def iconify_window(self, window: int) -> bool:
        if self._xlib is None or self._display is None:
            return False
        try:
            self._xlib.XIconifyWindow(self._display, int(window), self._screen)
            self._xlib.XFlush(self._display)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("X11 iconify failed: %s", exc)
            return False

    def toggle_maximize(self, window: int) -> bool:
        """Ask the window manager to add or remove the maximised state."""
        vertical = self._atom("_NET_WM_STATE_MAXIMIZED_VERT")
        horizontal = self._atom("_NET_WM_STATE_MAXIMIZED_HORZ")
        if not vertical or not horizontal:
            return False
        # Toggle (2) lets the window manager decide between maximise and restore.
        return self._send_client_message(
            window,
            "_NET_WM_STATE",
            (_NET_WM_STATE_TOGGLE, vertical, horizontal, 1, 0),
        )

    def activate_next_window(self) -> bool:
        """Activate the next window in the window manager's stacking list."""
        clients = self._window_list("_NET_CLIENT_LIST")
        if len(clients) < 2:
            return False
        active = self.active_window()
        index = clients.index(active) if active in clients else -1
        target = clients[(index + 1) % len(clients)]
        return self._send_client_message(
            target, "_NET_ACTIVE_WINDOW", (1, 0, 0, 0, 0)
        )
