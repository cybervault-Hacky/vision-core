"""Allowlisted application launcher.

Gesture data can never become a command. The launcher works exclusively from a
frozen allowlist of applications, and each entry resolves to a fixed argument
vector at startup:

* no shell is ever involved (``shell=True`` appears nowhere in this project),
* no gesture, hand coordinate, gesture name or configuration string is ever
  interpolated into the argument vector,
* the executable must resolve to an absolute path through ``shutil.which``
  (plus a small set of known absolute locations) before it may be spawned,
* an application key that is not in the allowlist is rejected outright.

Adding an application is a code change to :data:`ALLOWED_APPLICATIONS`, never a
configuration value and never a runtime input.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

logger = logging.getLogger("visioncore.controls.launcher")

# Windows executables that are sometimes installed outside PATH-based resolution.
_WINDOWS_KNOWN_PATHS: Mapping[str, Tuple[str, ...]] = {
    "mspaint.exe": ("%SystemRoot%\\System32\\mspaint.exe",),
    "notepad.exe": ("%SystemRoot%\\System32\\notepad.exe",),
}


@dataclass(frozen=True, slots=True)
class AllowedApplication:
    """One allowlisted application and its per-platform launch candidates."""

    key: str
    label: str
    candidates: Mapping[str, Tuple[Tuple[str, ...], ...]]

    def for_platform(self, platform: str) -> Tuple[Tuple[str, ...], ...]:
        return self.candidates.get(platform, ())


ALLOWED_APPLICATIONS: Tuple[AllowedApplication, ...] = (
    AllowedApplication(
        key="browser",
        label="BROWSER",
        candidates={
            "linux": (
                ("firefox",),
                ("google-chrome",),
                ("chromium",),
                ("chromium-browser",),
                ("brave-browser",),
                ("xdg-open", "about:blank"),
            ),
            "win32": (
                ("chrome.exe",),
                ("msedge.exe",),
                ("firefox.exe",),
                ("brave.exe",),
            ),
        },
    ),
    AllowedApplication(
        key="calculator",
        label="CALCULATOR",
        candidates={
            "linux": (
                ("gnome-calculator",),
                ("kcalc",),
                ("galculator",),
                ("xcalc",),
            ),
            "win32": (("calc.exe",),),
        },
    ),
    AllowedApplication(
        key="files",
        label="FILE MANAGER",
        candidates={
            "linux": (
                ("nautilus",),
                ("dolphin",),
                ("thunar",),
                ("nemo",),
                ("pcmanfm",),
                ("xdg-open", str(Path.home())),
            ),
            "win32": (("explorer.exe",),),
        },
    ),
)

# Short labels used by the compact HUD buttons.
ALLOWED_APPLICATIONS_BY_KEY: Mapping[str, AllowedApplication] = {
    application.key: application for application in ALLOWED_APPLICATIONS
}


def platform_key() -> str:
    """Coarse platform key used to select a candidate list."""
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


class ApplicationLauncher:
    """Resolves the allowlist once and spawns only those resolved programs."""

    def __init__(self) -> None:
        self.platform = platform_key()
        self.resolved: dict[str, Tuple[str, ...]] = {}
        self._resolve()

    # -- resolution -------------------------------------------------------- #

    @staticmethod
    def _locate(program: str) -> Optional[str]:
        """Absolute path of an allowlisted program, or ``None``."""
        candidate = Path(program)
        if candidate.is_absolute() and candidate.exists():
            return str(candidate)

        for override in _WINDOWS_KNOWN_PATHS.get(program.lower(), ()):
            expanded = os.path.expandvars(override)
            if Path(expanded).exists():
                return expanded

        found = shutil.which(program)
        return found or None

    def _resolve(self) -> None:
        for application in ALLOWED_APPLICATIONS:
            for argv in application.for_platform(self.platform):
                program = self._locate(argv[0])
                if program is None:
                    continue
                self.resolved[application.key] = (program, *argv[1:])
                break

    # -- capabilities ------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return bool(self.resolved)

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(self.resolved)

    def label(self, key: str) -> str:
        application = ALLOWED_APPLICATIONS_BY_KEY.get(key)
        return application.label if application else key.upper()

    # -- launching --------------------------------------------------------- #

    def launch(self, key: str) -> bool:
        """Start an allowlisted application. Unknown keys are rejected."""
        argv = self.resolved.get(key)
        if argv is None:
            logger.info("Application '%s' is not allowlisted or not installed", key)
            return False

        # Defence in depth: the argument vector must be a tuple of plain strings
        # with an absolute, existing executable. Nothing here can come from a
        # gesture.
        if not isinstance(argv, tuple) or not all(isinstance(part, str) for part in argv):
            logger.error("Refusing to launch a malformed argument vector for '%s'", key)
            return False
        if not Path(argv[0]).is_absolute() or not Path(argv[0]).exists():
            logger.error("Refusing to launch '%s': resolved path is not valid", key)
            return False

        try:
            subprocess.Popen(  # noqa: S603 - fixed allowlisted argv, never a shell
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except Exception as exc:
            logger.warning("Could not launch '%s': %s", key, exc)
            return False

        logger.info("Launched allowlisted application '%s' (%s)", key, argv[0])
        return True
