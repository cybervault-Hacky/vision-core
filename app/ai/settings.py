"""AI provider configuration, read from the environment only.

Secrets never enter this repository. The API key is read from
``VISIONCORE_AI_API_KEY`` (or the explicit ``api_key`` argument), is excluded
from every ``repr`` and log line, and is never written back to ``config.json`` -
which is why the AI settings deliberately live outside :class:`AppConfig`: a
configuration file that can be committed cannot carry an AI credential.

Configuration conceptually::

    VISIONCORE_AI_PROVIDER=openai|openai_compatible|none
    VISIONCORE_AI_API_KEY=<secret>
    VISIONCORE_AI_MODEL=gpt-4o-mini
    VISIONCORE_AI_BASE_URL=https://api.openai.com/v1
    VISIONCORE_AI_TIMEOUT_SEC=20
    VISIONCORE_AI_MAX_TOKENS=400
    VISIONCORE_AI_TEMPERATURE=0.2

With nothing set, VisionCore reports ``AI NOT CONFIGURED`` everywhere and the
rest of the application behaves exactly as before.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional

from app.ai.types import AISettings

logger = logging.getLogger("visioncore.ai")

ENV_PROVIDER = "VISIONCORE_AI_PROVIDER"
ENV_API_KEY = "VISIONCORE_AI_API_KEY"
ENV_MODEL = "VISIONCORE_AI_MODEL"
ENV_BASE_URL = "VISIONCORE_AI_BASE_URL"
ENV_TIMEOUT = "VISIONCORE_AI_TIMEOUT_SEC"
ENV_MAX_TOKENS = "VISIONCORE_AI_MAX_TOKENS"
ENV_TEMPERATURE = "VISIONCORE_AI_TEMPERATURE"

# Provider aliases that all speak the OpenAI chat completions shape.
_OPENAI_COMPATIBLE = ("openai", "openai_compatible", "openai-compatible", "compatible")
DISABLED_PROVIDERS = ("", "none", "disabled", "off")

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _float(value: Optional[str], fallback: float, low: float, high: float) -> float:
    if value is None or not str(value).strip():
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid numeric AI setting %r; using %.1f", value, fallback)
        return fallback
    if parsed != parsed:  # NaN
        return fallback
    return max(low, min(high, parsed))


def _int(value: Optional[str], fallback: int, low: int, high: int) -> int:
    if value is None or not str(value).strip():
        return fallback
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        logger.warning("Invalid numeric AI setting %r; using %d", value, fallback)
        return fallback
    return max(low, min(high, parsed))


def _http_url(value: Optional[str]) -> str:
    """Only http(s) endpoints are accepted (no file://, no other scheme)."""
    if value is None or not str(value).strip():
        return DEFAULT_BASE_URL
    candidate = str(value).strip().rstrip("/")
    if not (candidate.startswith("http://") or candidate.startswith("https://")):
        logger.warning("Rejected AI base URL with an unsupported scheme; using the default")
        return DEFAULT_BASE_URL
    return candidate


def normalise_provider(value: Optional[str]) -> str:
    """Map a provider name onto the supported set, defaulting to disabled."""
    if value is None:
        return "none"
    name = str(value).strip().lower()
    if name in DISABLED_PROVIDERS:
        return "none"
    if name in _OPENAI_COMPATIBLE:
        return "openai_compatible"
    logger.warning("Unknown AI provider %r; AI stays not configured", value)
    return "none"


def load_settings(environ: Optional[Mapping[str, str]] = None) -> AISettings:
    """Build the AI settings from the process environment."""
    env: Mapping[str, str] = os.environ if environ is None else environ

    provider = normalise_provider(env.get(ENV_PROVIDER))
    settings = AISettings(
        provider=provider,
        model=(env.get(ENV_MODEL) or "").strip(),
        base_url=_http_url(env.get(ENV_BASE_URL)),
        timeout_sec=_float(env.get(ENV_TIMEOUT), 20.0, 3.0, 120.0),
        max_tokens=_int(env.get(ENV_MAX_TOKENS), 400, 64, 4000),
        temperature=_float(env.get(ENV_TEMPERATURE), 0.2, 0.0, 1.5),
        api_key=(env.get(ENV_API_KEY) or "").strip(),
    )

    if provider != "none" and not settings.api_key:
        logger.warning(
            "AI provider %s selected without %s; AI stays not configured",
            settings.provider_label,
            ENV_API_KEY,
        )
        settings = AISettings(
            provider="none",
            model=settings.model,
            base_url=settings.base_url,
            timeout_sec=settings.timeout_sec,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            context_messages=settings.context_messages,
        )

    if settings.configured:
        logger.info(
            "AI provider ready: %s (model %s, key %s, timeout %.0fs)",
            settings.provider_label,
            settings.model or "provider default",
            settings.masked_key(),
            settings.timeout_sec,
        )
    else:
        # Honest, non-alarming: the rest of VisionCore is unaffected.
        logger.info("AI assistant not configured; local answers remain available")
    return settings
