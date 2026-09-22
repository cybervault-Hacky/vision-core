"""Unit tests for system diagnostics and platform detection."""

from __future__ import annotations

import pytest

from utils.diagnostics import SystemDiagnostics
from utils.platform import PlatformInfo


class TestPlatformInfo:
    """Validate system detection and permission troubleshooting advice."""

    def test_current_platform_detection(self):
        plat = PlatformInfo.current()
        assert plat.system != ""
        assert plat.python_version != ""
        assert plat.machine != ""
        assert isinstance(plat.is_headless, bool)

    def test_permission_hints_non_empty(self):
        plat = PlatformInfo.current()
        hint = plat.camera_permission_hint()
        assert isinstance(hint, str)
        assert len(hint) > 10


class TestSystemDiagnostics:
    """Validate telemetry generation and formatted report rendering."""

    def test_get_library_versions(self):
        libs = SystemDiagnostics.get_library_versions()
        assert "opencv" in libs
        assert "numpy" in libs
        assert "pygame" in libs
        # Since requirements are installed, versions shouldn't be "Not Installed"
        assert libs["opencv"] != "Not Installed"
        assert libs["numpy"] != "Not Installed"

    def test_collect_diagnostics_without_camera_probe(self):
        diag = SystemDiagnostics.collect(probe_camera=False)
        assert "platform" in diag
        assert "python" in diag
        assert "dependencies" in diag
        assert "display" in diag
        assert "camera" in diag

    def test_collect_diagnostics_with_camera_probe(self):
        # Should execute safely even if physical camera is absent
        diag = SystemDiagnostics.collect(camera_index=0, probe_camera=True)
        assert isinstance(diag["camera"], dict)
        assert "available" in diag["camera"]

    def test_format_report_rendering(self):
        report = SystemDiagnostics.format_report()
        assert "VISIONCORE SYSTEM DIAGNOSTICS" in report
        assert "Host OS" in report
        assert "OpenCV Version" in report
        assert "Camera Status" in report
        assert "Status Message" in report
