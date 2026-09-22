"""Unit tests for VisionCore configuration subsystem."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import AppConfig


class TestAppConfig:
    """Validate default settings, boundary validation, and serialization."""

    def test_default_values(self):
        cfg = AppConfig()
        assert cfg.camera_index == 0
        assert cfg.mirror_camera is True
        assert cfg.target_fps == 30
        assert cfg.show_debug is False
        assert cfg.window_width >= 800
        assert cfg.window_height >= 600
        assert cfg.boot_duration_sec > 0.5

    def test_validation_bounds_clamping(self):
        cfg = AppConfig(
            camera_index=-5,
            target_fps=500,
            window_width=300,
            window_height=200,
            boot_duration_sec=0.1,
            scan_animation_speed=-1.0,
        )
        cfg.validate()

        assert cfg.camera_index == 0
        assert cfg.target_fps == 120  # Clamped to upper bound
        assert cfg.window_width == cfg.min_window_width
        assert cfg.window_height == cfg.min_window_height
        assert cfg.boot_duration_sec == 0.5
        assert cfg.scan_animation_speed == 1.0

    def test_load_nonexistent_file_returns_defaults(self, tmp_path: Path):
        nonexistent = tmp_path / "does_not_exist.json"
        cfg = AppConfig.load(nonexistent)
        assert cfg.camera_index == 0
        assert cfg.mirror_camera is True

    def test_load_valid_json_file(self, tmp_path: Path):
        config_file = tmp_path / "test_config.json"
        data = {
            "camera_index": 2,
            "mirror_camera": False,
            "target_fps": 60,
            "show_debug": True,
            "window_width": 1280,
            "window_height": 720,
        }
        config_file.write_text(json.dumps(data), encoding="utf-8")

        cfg = AppConfig.load(config_file)
        assert cfg.camera_index == 2
        assert cfg.mirror_camera is False
        assert cfg.target_fps == 60
        assert cfg.show_debug is True
        assert cfg.window_width == 1280
        assert cfg.window_height == 720

    def test_load_malformed_json_fallback(self, tmp_path: Path):
        config_file = tmp_path / "broken.json"
        config_file.write_text("{broken-json: true,,", encoding="utf-8")

        cfg = AppConfig.load(config_file)
        # Should gracefully return valid default configuration
        assert isinstance(cfg, AppConfig)
        assert cfg.camera_index == 0
        assert cfg.target_fps == 30

    def test_save_and_reload(self, tmp_path: Path):
        config_file = tmp_path / "saved_config.json"
        cfg = AppConfig(camera_index=1, mirror_camera=False, target_fps=45)
        saved = cfg.save(config_file)
        assert saved is True
        assert config_file.exists()

        reloaded = AppConfig.load(config_file)
        assert reloaded.camera_index == 1
        assert reloaded.mirror_camera is False
        assert reloaded.target_fps == 45
