"""Single source of truth for the VisionCore release identity."""

from __future__ import annotations

PRODUCT_NAME = "VisionCore"
VERSION = "1.0.0"
__version__ = VERSION
DISPLAY_VERSION = f"{PRODUCT_NAME} v{VERSION}"

__all__ = ["DISPLAY_VERSION", "PRODUCT_NAME", "VERSION", "__version__"]
