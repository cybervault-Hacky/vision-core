"""Structured logging system for VisionCore."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# ANSI color codes for futuristic terminal telemetry
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"


class FuturisticFormatter(logging.Formatter):
    """Clean, high-tech terminal formatter for developer logs."""

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%H:%M:%S")

        level_name = record.levelname
        msg = record.getMessage()

        if self.use_color:
            if record.levelno >= logging.ERROR:
                level_str = f"{RED}{BOLD}{level_name:<7}{RESET}"
                msg = f"{RED}{msg}{RESET}"
            elif record.levelno >= logging.WARNING:
                level_str = f"{YELLOW}{BOLD}{level_name:<7}{RESET}"
            elif record.levelno == logging.INFO:
                level_str = f"{CYAN}{BOLD}{level_name:<7}{RESET}"
            else:
                level_str = f"{DIM}{level_name:<7}{RESET}"

            prefix = f"{DIM}[{timestamp}]{RESET} {level_str} {BOLD}VisionCore{RESET} ::"
        else:
            prefix = f"[{timestamp}] [{level_name:<7}] VisionCore ::"

        formatted = f"{prefix} {msg}"

        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        return formatted


def setup_logger(
    name: str = "visioncore",
    level: str | int = "INFO",
    log_file: Optional[Path | str] = None,
) -> logging.Logger:
    """Configure and return the root application logger."""
    if isinstance(level, str):
        level_val = getattr(logging, level.upper(), logging.INFO)
    else:
        level_val = level

    logger = logging.getLogger(name)
    logger.setLevel(level_val)

    # Avoid adding multiple duplicate handlers on re-configuration
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level_val)
    console_handler.setFormatter(FuturisticFormatter(use_color=True))
    logger.addHandler(console_handler)

    # Optional file handler (always plain text, no ANSI escape codes)
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(level_val)
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger
