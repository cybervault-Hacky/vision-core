"""Platform device backends for touchless device control.

The device layer talks only to this interface, so operating system specifics stay
in one place:

``WINDOWS``
    Media and volume keys through ``user32`` ``keybd_event``, master volume and
    mute through the ``IAudioEndpointVolume`` COM interface when it is available
    (falling back to volume keys when it is not), window actions on the
    foreground window through ``user32``. Backlight control is not implemented
    and is reported as unavailable rather than faked through display gamma.
``LINUX_X11``
    Media and volume keys through XTEST, volume and mute through the standard
    desktop audio tools (``wpctl``, ``pactl`` or ``amixer``) which also report the
    real level, backlight through the kernel ``sysfs`` interface, and window
    actions through ``libX11``/EWMH.
``UNSUPPORTED``
    Reports every capability as unavailable with a reason. Calls are accepted and
    do nothing, so the rest of the application stays safe and honest.

Security notes
--------------
* No shell is involved anywhere: audio helpers, window actions and application
  launches use fixed argument vectors with ``shell=True`` absent from the project.
* The only values ever substituted into a helper command are integers derived
  from the control layer's own clamped level - never a gesture, a coordinate or a
  configuration string.
* Nothing here requires elevated privileges, and no operating system permission
  is bypassed: if an interface is unavailable or refuses the request, the action
  reports failure and the interface says so.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import glob
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from app.controls.device_types import (
    CapabilityReport,
    DeviceCapability,
    MediaAction,
    WindowAction,
)
from app.controls.launcher import ApplicationLauncher
from app.controls.x11 import X11Session

logger = logging.getLogger("visioncore.controls.device_backend")

_HELPER_TIMEOUT = 1.5


def _run_helper(argv: Tuple[str, ...]) -> Optional[str]:
    """Run one allowlisted helper with a fixed argument vector.

    ``shell=True`` is never used and the argument vector is a module level
    constant. The only interpolated values in the whole layer are integers
    formatted from the controller's own clamped level.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed allowlisted argv, never a shell
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_HELPER_TIMEOUT,
            check=False,
        )
    except Exception as exc:
        logger.debug("Helper %s failed: %s", argv[0], exc)
        return None
    if completed.returncode != 0:
        logger.debug("Helper %s exited with %s", argv[0], completed.returncode)
        return None
    return completed.stdout or ""


class DeviceBackend:
    """Contract for a platform device backend."""

    name = "UNSUPPORTED"

    def __init__(self) -> None:
        self.available = False
        self.reason = "PLATFORM NOT SUPPORTED"
        self.detail = "No device backend for this platform"
        self._capabilities: dict[DeviceCapability, CapabilityReport] = {}

    # -- lifecycle --------------------------------------------------------- #

    def probe(self) -> bool:
        """Detect capabilities. Never raises."""
        return self.available

    def close(self) -> None:
        """Release native resources. Safe to call more than once."""

    # -- capabilities ------------------------------------------------------ #

    def capabilities(self) -> Tuple[CapabilityReport, ...]:
        order = (
            DeviceCapability.VOLUME,
            DeviceCapability.MEDIA,
            DeviceCapability.BRIGHTNESS,
            DeviceCapability.WINDOW,
            DeviceCapability.LAUNCHER,
        )
        return tuple(
            self._capabilities.get(
                capability,
                CapabilityReport(capability, False, "Not provided by this platform"),
            )
            for capability in order
        )

    def supports(self, capability: DeviceCapability) -> bool:
        report = self._capabilities.get(capability)
        return bool(report and report.available)

    def _set_capability(
        self, capability: DeviceCapability, available: bool, detail: str = ""
    ) -> None:
        self._capabilities[capability] = CapabilityReport(capability, available, detail)

    # -- audio ------------------------------------------------------------- #

    def volume_level(self) -> Optional[float]:
        """Master volume in ``0..1``, or ``None`` when the platform cannot report it."""
        return None

    def volume_set(self, level: float) -> bool:
        """Set the absolute master volume. False when unsupported."""
        return False

    def volume_step(self, direction: int) -> bool:
        """Relative nudge, used when the absolute level cannot be reported."""
        return False

    def mute_toggle(self) -> bool:
        return False

    def mute_state(self) -> Optional[bool]:
        return None

    # -- media ------------------------------------------------------------- #

    def media(self, action: MediaAction) -> bool:
        return False

    # -- display ----------------------------------------------------------- #

    def brightness_level(self) -> Optional[float]:
        return None

    def brightness_set(self, level: float) -> bool:
        return False

    # -- windows and applications ------------------------------------------ #

    def window(self, action: WindowAction) -> bool:
        return False

    def launchable(self) -> Tuple[Tuple[str, str], ...]:
        return ()

    def launch(self, key: str) -> bool:
        return False


class UnsupportedDeviceBackend(DeviceBackend):
    """Explicit no-op backend; every capability is reported unavailable."""

    name = "UNSUPPORTED"

    def __init__(self, reason: str = "PLATFORM NOT SUPPORTED", detail: str = "") -> None:
        super().__init__()
        self.reason = reason
        self.detail = detail or reason
        for capability in DeviceCapability:
            self._set_capability(capability, False, self.reason)

    def probe(self) -> bool:
        logger.info("Device control unavailable: %s", self.detail)
        return False


# --------------------------------------------------------------------------- #
# Linux: desktop audio tools, sysfs backlight, X11 keys and windows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _AudioToolSpec:
    """One allowlisted audio helper and how to talk to it."""

    name: str
    read_argv: Tuple[str, ...]
    set_template: Tuple[str, ...]
    mute_argv: Tuple[str, ...]
    parse_level: Callable[[str], Optional[float]]
    parse_mute: Callable[[str], Optional[bool]]


def _wpctl_level(output: str) -> Optional[float]:
    for token in output.replace(":", " ").split():
        try:
            value = float(token)
        except ValueError:
            continue
        # wpctl reports 0..1 (or 0..100+ when overamplified).
        return max(0.0, min(1.0, value))
    return None


def _wpctl_mute(output: str) -> Optional[bool]:
    return "[MUTED]" in output.upper() if output else None


def _pactl_level(output: str) -> Optional[float]:
    for token in output.split():
        if token.endswith("%"):
            try:
                return max(0.0, min(1.0, float(token.rstrip("%")) / 100.0))
            except ValueError:
                continue
    return None


def _pactl_mute(output: str) -> Optional[bool]:
    lowered = output.lower()
    if "yes" in lowered:
        return True
    if "no" in lowered:
        return False
    return None


def _amixer_level(output: str) -> Optional[float]:
    start = output.find("[")
    while start != -1:
        end = output.find("%]", start)
        if end != -1:
            try:
                return max(0.0, min(1.0, float(output[start + 1 : end]) / 100.0))
            except ValueError:
                pass
        start = output.find("[", start + 1)
    return None


def _amixer_mute(output: str) -> Optional[bool]:
    lowered = output.lower()
    if "[off]" in lowered:
        return True
    if "[on]" in lowered:
        return False
    return None


# Preference order: PipeWire, then PulseAudio, then bare ALSA.
_AUDIO_TOOLS: Tuple[_AudioToolSpec, ...] = (
    _AudioToolSpec(
        name="wpctl",
        read_argv=("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"),
        set_template=("wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "{}%"),
        mute_argv=("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"),
        parse_level=_wpctl_level,
        parse_mute=_wpctl_mute,
    ),
    _AudioToolSpec(
        name="pactl",
        read_argv=("pactl", "get-sink-volume", "@DEFAULT_SINK@"),
        set_template=("pactl", "set-sink-volume", "@DEFAULT_SINK@", "{}%"),
        mute_argv=("pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"),
        parse_level=_pactl_level,
        parse_mute=_pactl_mute,
    ),
    _AudioToolSpec(
        name="amixer",
        read_argv=("amixer", "get", "Master"),
        # ``-q`` keeps the helper from printing a full mixer dump on every step.
        set_template=("amixer", "-q", "sset", "Master", "{}%"),
        mute_argv=("amixer", "-q", "sset", "Master", "toggle"),
        parse_level=_amixer_level,
        parse_mute=_amixer_mute,
    ),
)


class LinuxDeviceBackend(DeviceBackend):
    """Volume, media, backlight, windows and allowlisted apps on Linux."""

    name = "LINUX_X11"

    def __init__(self) -> None:
        super().__init__()
        self._session = X11Session()
        self._audio: Optional[_AudioToolSpec] = None
        self._audio_level: Optional[float] = None
        self._backlight_path: Optional[Path] = None
        self._backlight_max: int = 0
        self._launcher = ApplicationLauncher()

    # -- probe ------------------------------------------------------------- #

    def probe(self) -> bool:
        if not sys.platform.startswith("linux"):
            self.reason = "LINUX BACKEND ON WRONG HOST"
            self.detail = "Linux device backend loaded on a non Linux host"
            return False

        x11 = self._session.open()
        self._probe_audio(x11)
        self._probe_backlight()
        self._probe_launcher()
        self._probe_windows(x11)

        self.available = any(report.available for report in self.capabilities())
        if self.available:
            self.reason = "READY"
            self.detail = "Linux device backend ready"
        else:
            self.reason = self._session.reason if not x11 else "NO DEVICE INTERFACES"
            self.detail = "No supported device interface was found on this host"
        return self.available

    def _probe_audio(self, x11: bool) -> None:
        for spec in _AUDIO_TOOLS:
            if shutil.which(spec.name) is None:
                continue
            output = _run_helper(spec.read_argv)
            if output is None:
                continue
            level = spec.parse_level(output)
            if level is None:
                continue
            self._audio = spec
            self._audio_level = level
            self._set_capability(
                DeviceCapability.VOLUME, True, f"{spec.name} reports the master level"
            )
            if x11 and self._session.keys_ready:
                self._set_capability(
                    DeviceCapability.MEDIA, True, "XTEST media keys"
                )
            return

        # No mixer helper: fall back to the desktop volume keys. They are still a
        # real volume change, but the level cannot be read back, so the interface
        # is told exactly that.
        if x11 and self._session.keys_ready and self._session.keycode("XF86AudioRaiseVolume"):
            self._set_capability(
                DeviceCapability.VOLUME, True, "volume keys (level not reportable)"
            )
            self._set_capability(DeviceCapability.MEDIA, True, "XTEST media keys")
        else:
            self._set_capability(
                DeviceCapability.VOLUME,
                False,
                "no mixer tool (wpctl, pactl, amixer) and no usable X keys",
            )
            self._set_capability(DeviceCapability.MEDIA, False, "media keys need an X session")
        return

    def _probe_backlight(self) -> None:
        candidates = sorted(glob.glob("/sys/class/backlight/*"))
        for directory in candidates:
            brightness = Path(directory) / "brightness"
            maximum = Path(directory) / "max_brightness"
            if not brightness.exists() or not maximum.exists():
                continue
            try:
                top = int(maximum.read_text().strip())
            except Exception:
                continue
            if top <= 0:
                continue
            if not os.access(brightness, os.W_OK):
                self._set_capability(
                    DeviceCapability.BRIGHTNESS,
                    False,
                    f"{Path(directory).name} is not writable by this user",
                )
                return
            self._backlight_path = brightness
            self._backlight_max = top
            self._set_capability(
                DeviceCapability.BRIGHTNESS, True, f"sysfs backlight ({Path(directory).name})"
            )
            return
        self._set_capability(
            DeviceCapability.BRIGHTNESS, False, "no writable sysfs backlight device"
        )

    def _probe_launcher(self) -> None:
        if self._launcher.available:
            names = ", ".join(self._launcher.keys)
            self._set_capability(DeviceCapability.LAUNCHER, True, f"allowlisted: {names}")
        else:
            self._set_capability(
                DeviceCapability.LAUNCHER, False, "no allowlisted application is installed"
            )

    def _probe_windows(self, x11: bool) -> None:
        if x11 and self._session.window_ready:
            self._set_capability(
                DeviceCapability.WINDOW, True, "X11 and EWMH window manager"
            )
        else:
            self._set_capability(
                DeviceCapability.WINDOW, False, "window actions need an X session"
            )

    def close(self) -> None:
        self._session.close()

    # -- audio ------------------------------------------------------------- #

    def volume_level(self) -> Optional[float]:
        if self._audio is None:
            return None
        output = _run_helper(self._audio.read_argv)
        if output is None:
            return None
        level = self._audio.parse_level(output)
        if level is not None:
            self._audio_level = level
        mute = self._audio.parse_mute(output)
        if mute is not None:
            self._muted = mute
        return level

    _muted: Optional[bool] = None

    def volume_set(self, level: float) -> bool:
        if self._audio is None:
            return False
        # Only an integer percentage derived from the controller's own clamped
        # level is ever substituted into the fixed argument vector.
        percent = max(0, min(100, int(round(level * 100))))
        argv = tuple(part.format(percent) for part in self._audio.set_template)
        if _run_helper(argv) is None:
            return False
        self._audio_level = percent / 100.0
        return True

    def volume_step(self, direction: int) -> bool:
        if not self._session.keys_ready:
            return False
        keysym = "XF86AudioRaiseVolume" if direction > 0 else "XF86AudioLowerVolume"
        if not self._session.fake_key(keysym, True):
            return False
        return self._session.fake_key(keysym, False)

    def mute_toggle(self) -> bool:
        if self._audio is not None:
            if _run_helper(self._audio.mute_argv) is None:
                return False
            self._muted = None if self._muted is None else not self._muted
            return True
        if not self._session.keys_ready:
            return False
        if not self._session.fake_key("XF86AudioMute", True):
            return False
        return self._session.fake_key("XF86AudioMute", False)

    def mute_state(self) -> Optional[bool]:
        return self._muted

    # -- media ------------------------------------------------------------- #

    def media(self, action: MediaAction) -> bool:
        if not self._session.keys_ready:
            return False
        keysym = {
            MediaAction.PLAY_PAUSE: "XF86AudioPlay",
            MediaAction.NEXT: "XF86AudioNext",
            MediaAction.PREVIOUS: "XF86AudioPrev",
        }[action]
        if not self._session.fake_key(keysym, True):
            return False
        return self._session.fake_key(keysym, False)

    # -- display ----------------------------------------------------------- #

    def brightness_level(self) -> Optional[float]:
        if self._backlight_path is None or self._backlight_max <= 0:
            return None
        try:
            current = int(self._backlight_path.read_text().strip())
        except Exception as exc:
            logger.debug("Could not read backlight level: %s", exc)
            return None
        return max(0.0, min(1.0, current / self._backlight_max))

    def brightness_set(self, level: float) -> bool:
        if self._backlight_path is None or self._backlight_max <= 0:
            return False
        value = max(1, min(self._backlight_max, int(round(level * self._backlight_max))))
        try:
            self._backlight_path.write_text(f"{value}\n")
        except Exception as exc:
            logger.warning("Could not set backlight level: %s", exc)
            return False
        return True

    # -- windows and applications ------------------------------------------ #

    def window(self, action: WindowAction) -> bool:
        if not self._session.window_ready:
            return False
        if action is WindowAction.NEXT:
            return self._session.activate_next_window()
        window = self._session.active_window()
        if window is None:
            return False
        if action is WindowAction.MINIMIZE:
            return self._session.iconify_window(window)
        if action is WindowAction.MAXIMIZE:
            return self._session.toggle_maximize(window)
        return False

    def launchable(self) -> Tuple[Tuple[str, str], ...]:
        return tuple((key, self._launcher.label(key)) for key in self._launcher.keys)

    def launch(self, key: str) -> bool:
        return self._launcher.launch(key)


# --------------------------------------------------------------------------- #
# Windows: media keys, master volume COM interface, window actions
# --------------------------------------------------------------------------- #


class _WindowsAudio:
    """Master volume and mute through ``IAudioEndpointVolume``.

    Every call is guarded: if the interface cannot be created (no audio endpoint,
    unusual session) the caller falls back to the volume keys and reports the
    level as not reportable instead of pretending to know it.
    """

    _CLSCTX_ALL = 0x17
    _E_RENDER = 0
    _E_MULTIMEDIA = 1

    def __init__(self) -> None:
        self.available = False
        self.reason = "audio endpoint interface unavailable"
        self._ole32 = None
        self._enumerator: Optional[ctypes.c_void_p] = None
        self._device: Optional[ctypes.c_void_p] = None
        self._volume: Optional[ctypes.c_void_p] = None
        self._initialised = False

    class _Guid(ctypes.Structure):
        _fields_ = (
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        )

    @classmethod
    def _guid(cls, data1: int, data2: int, data3: int, tail: Tuple[int, ...]):
        return cls._Guid(data1, data2, data3, (ctypes.c_ubyte * 8)(*tail))

    @staticmethod
    def _call(pointer, index: int, restype, argtypes, *args):
        vtable = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return prototype(vtable[index])(pointer, *args)

    def probe(self) -> bool:  # pragma: no cover - Windows only
        try:
            ole32 = ctypes.WinDLL("ole32")
            self._ole32 = ole32
            ole32.CoInitialize(None)
            self._initialised = True

            clsid = self._guid(0xBCDE0395, 0xE52F, 0x467C, (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))
            iid_enumerator = self._guid(0xA95664D2, 0x9614, 0x4F35, (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))
            iid_volume = self._guid(0x5CDF2C82, 0x841E, 0x4546, (0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A))

            enumerator = ctypes.c_void_p()
            ole32.CoCreateInstance(
                ctypes.byref(clsid), None, self._CLSCTX_ALL,
                ctypes.byref(iid_enumerator), ctypes.byref(enumerator),
            )
            if not enumerator:
                return False
            self._enumerator = enumerator

            device = ctypes.c_void_p()
            self._call(
                enumerator, 4, ctypes.c_long,
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                self._E_RENDER, self._E_MULTIMEDIA, ctypes.byref(device),
            )
            if not device:
                return False
            self._device = device

            volume = ctypes.c_void_p()
            self._call(
                device, 3, ctypes.c_long,
                [
                    ctypes.POINTER(self._Guid), ctypes.c_ulong, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p),
                ],
                ctypes.byref(iid_volume), self._CLSCTX_ALL, None, ctypes.byref(volume),
            )
            if not volume:
                return False
            self._volume = volume
            self.available = True
            self.reason = "IAudioEndpointVolume"
            return True
        except Exception as exc:  # pragma: no cover - Windows only
            self.reason = f"audio interface unavailable ({exc})"
            return False

    def level(self) -> Optional[float]:  # pragma: no cover - Windows only
        if not self.available:
            return None
        try:
            value = ctypes.c_float(0.0)
            self._call(self._volume, 9, ctypes.c_long, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(value))
            return max(0.0, min(1.0, float(value.value)))
        except Exception:
            return None

    def set_level(self, level: float) -> bool:  # pragma: no cover - Windows only
        if not self.available:
            return False
        try:
            self._call(
                self._volume, 7, ctypes.c_long,
                [ctypes.c_float, ctypes.c_void_p],
                max(0.0, min(1.0, float(level))), None,
            )
            return True
        except Exception:
            return False

    def mute(self) -> Optional[bool]:  # pragma: no cover - Windows only
        if not self.available:
            return None
        try:
            value = ctypes.c_int(0)
            self._call(self._volume, 15, ctypes.c_long, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(value))
            return bool(value.value)
        except Exception:
            return None

    def set_mute(self, muted: bool) -> bool:  # pragma: no cover - Windows only
        if not self.available:
            return False
        try:
            self._call(
                self._volume, 14, ctypes.c_long,
                [ctypes.c_int, ctypes.c_void_p], 1 if muted else 0, None,
            )
            return True
        except Exception:
            return False

    def close(self) -> None:  # pragma: no cover - Windows only
        for pointer in (self._volume, self._device, self._enumerator):
            if pointer:
                try:
                    self._call(pointer, 2, ctypes.c_ulong, [])
                except Exception:
                    pass
        self._volume = self._device = self._enumerator = None
        if self._initialised and self._ole32 is not None:
            try:
                self._ole32.CoUninitialize()
            except Exception:
                pass
        self._initialised = False


class WindowsDeviceBackend(DeviceBackend):
    """Media keys, master volume, window actions and allowlisted apps on Windows."""

    name = "WINDOWS"

    _VK_MEDIA_NEXT = 0xB0
    _VK_MEDIA_PREV = 0xB1
    _VK_MEDIA_PLAY_PAUSE = 0xB3
    _VK_VOLUME_MUTE = 0xAD
    _VK_VOLUME_DOWN = 0xAE
    _VK_VOLUME_UP = 0xAF
    _VK_MENU = 0x12
    _VK_TAB = 0x09
    _KEYEVENTF_KEYUP = 0x0002
    _SW_MINIMIZE = 6
    _SW_MAXIMIZE = 3

    def __init__(self) -> None:
        super().__init__()
        self._user32 = None
        self._audio = _WindowsAudio()
        self._launcher = ApplicationLauncher()

    def probe(self) -> bool:  # pragma: no cover - Windows only
        if sys.platform != "win32":
            self.reason = "WINDOWS BACKEND ON WRONG HOST"
            self.detail = "Windows device backend loaded on a non Windows host"
            return False

        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        except Exception as exc:
            self.reason = "USER32 UNAVAILABLE"
            self.detail = f"user32 could not be loaded ({exc})"
            return False

        self._set_capability(DeviceCapability.MEDIA, True, "media keys (keybd_event)")
        if self._audio.probe():
            self._set_capability(DeviceCapability.VOLUME, True, self._audio.reason)
        else:
            self._set_capability(
                DeviceCapability.VOLUME, True,
                f"volume keys (level not reportable: {self._audio.reason})",
            )
        self._set_capability(
            DeviceCapability.BRIGHTNESS, False,
            "backlight control needs vendor APIs and is not implemented",
        )
        self._set_capability(DeviceCapability.WINDOW, True, "user32 window state")
        if self._launcher.available:
            self._set_capability(
                DeviceCapability.LAUNCHER, True, f"allowlisted: {', '.join(self._launcher.keys)}"
            )
        else:
            self._set_capability(DeviceCapability.LAUNCHER, False, "no allowlisted application found")

        self.available = True
        self.reason = "READY"
        self.detail = "Windows device backend ready"
        return True

    def close(self) -> None:
        self._audio.close()

    def _key(self, virtual_key: int) -> bool:  # pragma: no cover - Windows only
        try:
            self._user32.keybd_event(virtual_key, 0, 0, 0)
            self._user32.keybd_event(virtual_key, 0, self._KEYEVENTF_KEYUP, 0)
            return True
        except Exception as exc:
            logger.warning("Windows key event failed: %s", exc)
            return False

    def volume_level(self) -> Optional[float]:  # pragma: no cover - Windows only
        return self._audio.level()

    def volume_set(self, level: float) -> bool:  # pragma: no cover - Windows only
        if self._audio.set_level(level):
            return True
        # Without the COM interface only relative keys are possible.
        return self._key(self._VK_VOLUME_UP if level > 0.5 else self._VK_VOLUME_DOWN)

    def volume_step(self, direction: int) -> bool:  # pragma: no cover - Windows only
        return self._key(self._VK_VOLUME_UP if direction > 0 else self._VK_VOLUME_DOWN)

    def mute_toggle(self) -> bool:  # pragma: no cover - Windows only
        state = self._audio.mute()
        if state is not None and self._audio.set_mute(not state):
            return True
        return self._key(self._VK_VOLUME_MUTE)

    def mute_state(self) -> Optional[bool]:  # pragma: no cover - Windows only
        return self._audio.mute()

    def media(self, action: MediaAction) -> bool:  # pragma: no cover - Windows only
        key = {
            MediaAction.PLAY_PAUSE: self._VK_MEDIA_PLAY_PAUSE,
            MediaAction.NEXT: self._VK_MEDIA_NEXT,
            MediaAction.PREVIOUS: self._VK_MEDIA_PREV,
        }[action]
        return self._key(key)

    def window(self, action: WindowAction) -> bool:  # pragma: no cover - Windows only
        if self._user32 is None:
            return False
        try:
            if action is WindowAction.NEXT:
                # Alt+Tab is the standard shell-level application switch.
                self._user32.keybd_event(self._VK_MENU, 0, 0, 0)
                self._user32.keybd_event(self._VK_TAB, 0, 0, 0)
                self._user32.keybd_event(self._VK_TAB, 0, self._KEYEVENTF_KEYUP, 0)
                self._user32.keybd_event(self._VK_MENU, 0, self._KEYEVENTF_KEYUP, 0)
                return True
            handle = self._user32.GetForegroundWindow()
            if not handle:
                return False
            command = self._SW_MINIMIZE if action is WindowAction.MINIMIZE else self._SW_MAXIMIZE
            return bool(self._user32.ShowWindow(handle, command))
        except Exception as exc:
            logger.warning("Windows window action failed: %s", exc)
            return False

    def launchable(self) -> Tuple[Tuple[str, str], ...]:
        return tuple((key, self._launcher.label(key)) for key in self._launcher.keys)

    def launch(self, key: str) -> bool:
        return self._launcher.launch(key)


def create_device_backend() -> DeviceBackend:
    """Create and probe the device backend for the current platform."""
    if sys.platform.startswith("win"):
        backend: DeviceBackend = WindowsDeviceBackend()
    elif sys.platform.startswith("linux"):
        backend = LinuxDeviceBackend()
    else:
        backend = UnsupportedDeviceBackend(
            "PLATFORM NOT SUPPORTED", f"Platform '{sys.platform}' has no device backend"
        )

    if isinstance(backend, UnsupportedDeviceBackend):
        backend.probe()
    elif backend.probe():
        logger.info("Device control backend '%s' ready", backend.name)
    else:
        logger.info(
            "Device control backend '%s' unavailable: %s", backend.name, backend.detail
        )
    return backend


__all__ = [
    "DeviceBackend",
    "LinuxDeviceBackend",
    "UnsupportedDeviceBackend",
    "WindowsDeviceBackend",
    "create_device_backend",
]
