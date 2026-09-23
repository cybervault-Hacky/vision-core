#!/usr/bin/env python3
"""
VisionCore — local-first computer vision system.
Main entry point.
"""

from __future__ import annotations

import argparse
import os
import sys

from version import DISPLAY_VERSION

# Suppress Pygame and OpenCV noise before importing
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")


def verify_python_version() -> None:
    """Ensure runtime environment meets the Python 3.10+ requirement."""
    if sys.version_info < (3, 10):
        major = sys.version_info[0]
        minor = sys.version_info[1]
        micro = sys.version_info[2] if len(sys.version_info) > 2 else 0
        sys.stderr.write(
            f"ERROR: VisionCore requires Python 3.10 or newer.\n"
            f"Detected Python {major}.{minor}.{micro}.\n"
            f"Please update your Python environment.\n"
        )
        sys.exit(1)


def check_dependencies() -> None:
    """Validate required runtime packages before constructing the application.

    Imports are checked here instead of allowing a deep module import to fail
    with an opaque traceback. Optional AI and voice packages are intentionally
    absent from this check: the application reports those capabilities honestly
    when they are not configured or installed.
    """
    missing = []
    notes = []

    checks = (
        ("opencv-python-headless (or opencv-python)", "cv2"),
        ("numpy", "numpy"),
        ("pygame", "pygame"),
        ("mediapipe", "mediapipe"),
    )
    for package, module in checks:
        try:
            __import__(module)
        except Exception as exc:
            detail = f" ({type(exc).__name__}: {exc})" if str(exc) else f" ({type(exc).__name__})"
            missing.append(f"{package}{detail}")
            if module == "cv2" and "libGL" in str(exc):
                # The OpenCV GUI build needs a system OpenGL library that
                # headless hosts do not ship. Point at the fix instead of a
                # generic failure.
                notes.append(
                    "OpenCV loaded a GUI build that needs libGL. Either install the "
                    "system library (Debian/Ubuntu: sudo apt install -y libgl1) or run:\n"
                    "    python3 -m pip install --force-reinstall --no-deps 'opencv-python-headless>=4.8.0,<4.12'"
                )

    if missing:
        sys.stderr.write(
            "\n[VISIONCORE DEPENDENCY ERROR]\n"
            "The following required packages are missing or unusable:\n"
            + "".join(f"  - {pkg}\n" for pkg in missing)
            + "\nPlease install required dependencies by running:\n"
            "  python3 -m pip install -r requirements.txt\n"
        )
        if notes:
            sys.stderr.write(
                "\nAdditional note:\n  " + "\n  ".join(notes) + "\n"
            )
        sys.stderr.write("\n")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    """Parse optional CLI flags while keeping standard execution argument-free."""
    parser = argparse.ArgumentParser(
        prog="visioncore",
        description=f"{DISPLAY_VERSION} — local-first computer vision system.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=DISPLAY_VERSION,
        help="Show the VisionCore release version and exit.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print system diagnostics and camera probe telemetry, then exit.",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=None,
        help="Override camera hardware index (default: 0).",
    )
    parser.add_argument(
        "--mock-camera",
        action="store_true",
        help="Use synthetic calibration stream instead of physical camera hardware.",
    )
    parser.add_argument(
        "--max-hands",
        type=int,
        default=None,
        help="Maximum number of simultaneously tracked hands (default: 1, max: 4).",
    )
    parser.add_argument(
        "--no-tracking",
        action="store_true",
        help="Run the camera HUD without the local hand tracking pipeline.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return parser.parse_args()


def main() -> int:
    """Primary application entry point."""
    verify_python_version()
    # Parse help/version/diagnostics before importing or validating the desktop
    # runtime. These commands remain useful in a clean environment where the
    # optional display stack has not been installed yet.
    args = parse_args()

    if args.diagnostics:
        from utils.diagnostics import SystemDiagnostics
        cam_idx = args.camera_index if args.camera_index is not None else 0
        print(SystemDiagnostics.format_report(camera_index=cam_idx))
        return 0

    check_dependencies()

    try:
        from app.application import Application
        from app.config import AppConfig

        config = AppConfig.load()
        if args.camera_index is not None:
            config.camera_index = args.camera_index
        if args.max_hands is not None:
            config.max_hands = args.max_hands
            config.validate()
        if args.no_tracking:
            config.tracking_enabled = False
        if args.mock_camera:
            config.mock_camera = True
        if args.debug:
            os.environ["OPENCV_LOG_LEVEL"] = "DEBUG"
            config.show_debug = True
            config.log_level = "DEBUG"

        app = Application(config=config)
        return app.run()

    except KeyboardInterrupt:
        print("\nVisionCore interrupted by user. Exiting cleanly.")
        return 0
    except Exception as exc:
        sys.stderr.write(f"\n[FATAL ERROR] An unexpected error occurred: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
