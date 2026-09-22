"""System diagnostics and hardware inspection for VisionCore."""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from utils.platform import PlatformInfo


class SystemDiagnostics:
    """Collects system, runtime, library, and camera diagnostic telemetry."""

    @staticmethod
    def get_library_versions() -> Dict[str, str]:
        """Query versions of third-party libraries safely."""
        versions: Dict[str, str] = {}

        try:
            import cv2
            versions["opencv"] = cv2.__version__
        except ImportError:
            versions["opencv"] = "Not Installed"

        try:
            import numpy as np
            versions["numpy"] = np.__version__
        except ImportError:
            versions["numpy"] = "Not Installed"

        try:
            import pygame
            versions["pygame"] = pygame.__version__
        except ImportError:
            versions["pygame"] = "Not Installed"

        return versions

    @classmethod
    def probe_camera_device(cls, camera_index: int = 0) -> Dict[str, Any]:
        """Perform a quick probe on the specified camera index."""
        result: Dict[str, Any] = {
            "index": camera_index,
            "available": False,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "backend": "UNKNOWN",
            "message": "Not probed",
        }

        try:
            import cv2
        except ImportError:
            result["message"] = "OpenCV is not installed"
            return result

        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                result["message"] = f"Camera index {camera_index} could not be opened"
                cap.release()
                return result

            # Attempt to read a single validation frame
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                result["message"] = f"Camera index {camera_index} opened but returned no frames"
                cap.release()
                return result

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            backend_name = cap.getBackendName() if hasattr(cap, "getBackendName") else "OpenCV VideoCapture"

            cap.release()

            result.update({
                "available": True,
                "width": width if width > 0 else frame.shape[1],
                "height": height if height > 0 else frame.shape[0],
                "fps": fps if fps > 0 else 30.0,
                "backend": backend_name,
                "message": "Device ready and returning frames",
            })
            return result

        except Exception as exc:
            result["message"] = f"Exception during camera probe: {exc}"
            return result

    @classmethod
    def collect(cls, camera_index: int = 0, probe_camera: bool = True) -> Dict[str, Any]:
        """Aggregate full system, platform, dependency, and camera diagnostics."""
        platform_info = PlatformInfo.current()
        libs = cls.get_library_versions()

        camera_data = (
            cls.probe_camera_device(camera_index)
            if probe_camera
            else {"index": camera_index, "probed": False}
        )

        display_driver = "None"
        try:
            import os
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
            if pygame.display.get_init():
                display_driver = pygame.display.get_driver() or "unknown"
            else:
                display_driver = "pygame display not initialized"
        except Exception:
            display_driver = "unavailable"

        return {
            "platform": {
                "system": platform_info.system,
                "release": platform_info.release,
                "machine": platform_info.machine,
                "display_server": platform_info.display_server,
                "is_headless": platform_info.is_headless,
            },
            "python": {
                "version": platform_info.python_version,
                "compiler": platform_info.python_compiler,
                "executable": sys.executable,
            },
            "dependencies": libs,
            "display": {
                "driver": display_driver,
            },
            "camera": camera_data,
        }

    @classmethod
    def format_report(cls, data: Optional[Dict[str, Any]] = None, camera_index: int = 0) -> str:
        """Render diagnostics data into a clean, sci-fi formatted developer report."""
        if data is None:
            data = cls.collect(camera_index=camera_index, probe_camera=True)

        plat = data.get("platform", {})
        py = data.get("python", {})
        deps = data.get("dependencies", {})
        cam = data.get("camera", {})
        disp = data.get("display", {})

        cam_status = "ONLINE" if cam.get("available") else "UNAVAILABLE"
        cam_res = f"{cam.get('width', 0)}x{cam.get('height', 0)}" if cam.get("available") else "N/A"
        cam_fps = f"{cam.get('fps', 0):.1f}" if cam.get("available") else "N/A"

        lines = [
            "+=============================================================+",
            "|               VISIONCORE SYSTEM DIAGNOSTICS                 |",
            "+=============================================================+",
            f"| Host OS          : {plat.get('system')} {plat.get('release')} ({plat.get('machine')})",
            f"| Display Server   : {plat.get('display_server')}",
            f"| Display Driver   : {disp.get('driver')}",
            f"| Python Version   : {py.get('version')} ({py.get('compiler')})",
            "+-------------------------------------------------------------+",
            f"| OpenCV Version   : {deps.get('opencv')}",
            f"| NumPy Version    : {deps.get('numpy')}",
            f"| Pygame Version   : {deps.get('pygame')}",
            "+-------------------------------------------------------------+",
            f"| Camera Index     : {cam.get('index', 0)}",
            f"| Camera Status    : {cam_status}",
            f"| Resolution       : {cam_res}",
            f"| Hardware FPS     : {cam_fps}",
            f"| Backend Engine   : {cam.get('backend', 'N/A')}",
            f"| Status Message   : {cam.get('message', 'N/A')}",
            "+=============================================================+",
        ]
        return "\n".join(lines)
