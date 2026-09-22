"""Unit tests for futuristic logger."""

from __future__ import annotations

import logging
from pathlib import Path

from app.logger import FuturisticFormatter, setup_logger


def test_futuristic_formatter_plain():
    formatter = FuturisticFormatter(use_color=False)
    record = logging.LogRecord(
        name="visioncore.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "VisionCore :: Test message" in formatted
    assert "[INFO   ]" in formatted


def test_setup_logger_with_file(tmp_path: Path):
    log_file = tmp_path / "test.log"
    logger = setup_logger("test_vc", level="DEBUG", log_file=log_file)
    logger.debug("Debug entry")
    logger.info("Info entry")

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Debug entry" in content
    assert "Info entry" in content
