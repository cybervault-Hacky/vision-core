"""Unit tests for main CLI parsing and dependency verification."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import main


def test_verify_python_version_success():
    # Should not raise or exit when Python is >= 3.10
    main.verify_python_version()


def test_verify_python_version_failure():
    with patch.object(sys, "version_info", (3, 9, 0)):
        with pytest.raises(SystemExit) as exc:
            main.verify_python_version()
        assert exc.value.code == 1


def test_check_dependencies_success():
    # In this environment all core dependencies are installed
    main.check_dependencies()


def test_check_dependencies_missing():
    with patch.dict(sys.modules, {"cv2": None}):
        with pytest.raises(SystemExit) as exc:
            main.check_dependencies()
        assert exc.value.code == 1


def test_parse_args_defaults():
    with patch.object(sys, "argv", ["main.py"]):
        args = main.parse_args()
        assert args.diagnostics is False
        assert args.camera_index is None
        assert args.mock_camera is False
        assert args.debug is False


def test_parse_args_custom():
    with patch.object(
        sys,
        "argv",
        ["main.py", "--diagnostics", "--camera-index", "2", "--mock-camera", "--debug"],
    ):
        args = main.parse_args()
        assert args.diagnostics is True
        assert args.camera_index == 2
        assert args.mock_camera is True
        assert args.debug is True


def test_main_diagnostics_flag(capsys):
    with patch.object(sys, "argv", ["main.py", "--diagnostics"]):
        code = main.main()
        assert code == 0
        captured = capsys.readouterr()
        assert "VISIONCORE SYSTEM DIAGNOSTICS" in captured.out
