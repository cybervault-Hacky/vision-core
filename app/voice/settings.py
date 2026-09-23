"""Voice input configuration, read from the environment only.

Voice is off unless the user activates it, and it can be disabled entirely:

    VISIONCORE_SPEECH_PROVIDER=auto|vosk|sphinx|none
    VISIONCORE_SPEECH_MODEL=/path/to/offline/model      (vosk)
    VISIONCORE_SPEECH_DEVICE=2                          (input device index)
    VISIONCORE_SPEECH_TIMEOUT_SEC=8                     (max listening window)
    VISIONCORE_SPEECH_PHRASE_LIMIT_SEC=15               (longest utterance)
    VISIONCORE_SPEECH_SAMPLE_RATE=16000

``auto`` (the default) takes the first *local* engine that is actually usable on
this machine: Vosk with a model directory, then PocketSphinx through the
SpeechRecognition package, then nothing - and "nothing" is reported honestly as
``VOICE UNAVAILABLE`` rather than hidden.

Which engine is chosen is a property of the machine, not of the repository, so -
like the AI settings - nothing here belongs in a commit. ``none`` switches voice
input off completely; the rest of VisionCore is unaffected either way.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional

from app.voice.types import SpeechSettings

logger = logging.getLogger("visioncore.voice")

ENV_PROVIDER = "VISIONCORE_SPEECH_PROVIDER"
ENV_MODEL = "VISIONCORE_SPEECH_MODEL"
ENV_DEVICE = "VISIONCORE_SPEECH_DEVICE"
ENV_TIMEOUT = "VISIONCORE_SPEECH_TIMEOUT_SEC"
ENV_PHRASE_LIMIT = "VISIONCORE_SPEECH_PHRASE_LIMIT_SEC"
ENV_SAMPLE_RATE = "VISIONCORE_SPEECH_SAMPLE_RATE"

PROVIDER_ALIASES = {
    "auto": "auto",
    "": "auto",
    "vosk": "vosk",
    "sphinx": "sphinx",
    "pocketsphinx": "sphinx",
    "speech_recognition": "sphinx",
    "none": "none",
    "off": "none",
    "disabled": "none",
}


def _float(value: Optional[str], fallback: float, low: float, high: float) -> float:
    if value is None or not str(value).strip():
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid numeric speech setting %r; using %.1f", value, fallback)
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
        logger.warning("Invalid numeric speech setting %r; using %d", value, fallback)
        return fallback
    return max(low, min(high, parsed))


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or not str(value).strip():
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid speech device index %r", value)
        return None


def normalise_engine(value: Optional[str]) -> str:
    """Map an engine name onto the supported set (unknown -> auto)."""
    if value is None:
        return "auto"
    name = str(value).strip().lower()
    if name in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[name]
    logger.warning("Unknown speech provider %r; falling back to auto detection", value)
    return "auto"


def load_speech_settings(environ: Optional[Mapping[str, str]] = None) -> SpeechSettings:
    """Build the voice settings from the process environment."""
    env: Mapping[str, str] = os.environ if environ is None else environ

    engine = normalise_engine(env.get(ENV_PROVIDER))
    settings = SpeechSettings(
        engine=engine,
        model_path=(env.get(ENV_MODEL) or "").strip(),
        device_index=_optional_int(env.get(ENV_DEVICE)),
        sample_rate=_int(env.get(ENV_SAMPLE_RATE), 16000, 8000, 48000),
        listen_limit_sec=_float(env.get(ENV_TIMEOUT), 8.0, 2.0, 30.0),
        phrase_limit_sec=_float(env.get(ENV_PHRASE_LIMIT), 15.0, 2.0, 30.0),
    )

    if not settings.enabled:
        logger.info("Voice input disabled in configuration; the microphone stays closed")
    return settings


def describe(settings: SpeechSettings) -> str:
    """Short, secret-free description for logs and diagnostics."""
    if not settings.enabled:
        return "NONE (disabled in configuration)"
    model = os.path.basename(settings.model_path.rstrip("/")) if settings.model_path else ""
    return (
        f"{settings.engine.upper()}"
        + (f" (model {model})" if model else "")
        + f", listening window {settings.listen_limit_sec:.0f}s"
    )
