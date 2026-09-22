#!/usr/bin/env python3
"""
VisionCore — Touchless Computer Control System (Phase 1)
Main entry point.
"""

from __future__ import annotations

import argparse
import os
import sys

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
    """Validate that required core packages are installed before booting."""
    missing = []
    try:
        import cv2  # noqa: F401
    except ImportError:
        missing.append("opencv-python-headless (or opencv-python)")

    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")

    try:
        import pygame  # noqa: F401
    except ImportError:
        missing.append("pygame")

    if missing:
        sys.stderr.write(
            "\n[VISIONCORE DEPENDENCY ERROR]\n"
            "The following required packages are missing:\n"
            + "".join(f"  • {pkg}\n" for pkg in missing)
            + "\nPlease install required dependencies by running:\n"
            "  pip install -r requirements.txt\n\n"
        )
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    """Parse optional CLI flags while keeping standard execution argument-free."""
    parser = argparse.ArgumentParser(
        prog="visioncore",
        description="VisionCore — Touchless Computer Control System (Phase 1)",
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
        help="Use synthetic calibration camera stream (ideal for headless or testing).",
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
    check_dependencies()

    args = parse_args()

    if args.diagnostics:
        from utils.diagnostics import SystemDiagnostics
        cam_idx = args.camera_index if args.camera_index is not None else 0
        print(SystemDiagnostics.format_report(camera_index=cam_idx))
        return 0

    try:
        from app.application import Application
        from app.config import AppConfig

        config = AppConfig.load()
        if args.camera_index is not None:
            config.camera_index = args.camera_index
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
