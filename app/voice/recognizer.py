"""Replaceable speech recognition providers - offline only, by design.

The contract is small on purpose::

    SpeechRecognizer.listen(cancel, on_capture_complete) -> SpeechResult

An implementation opens the microphone, captures until the speaker stops (or the
configured window expires), transcribes **locally**, and returns an honest
:class:`~app.voice.types.SpeechResult`. The application never touches a
microphone API, and the recognition engine is swappable without changing a line
of the assistant, the HUD or the safety layer.

Two properties this module guarantees:

* **No cloud path exists.** Vosk and PocketSphinx are offline engines, and the
  cloud recognisers that the optional ``speech_recognition`` package also offers
  are never called. Audio cannot leave the machine through this code.
* **Capture is short lived and cancellable.** Audio is captured in small chunks,
  so a cancellation stops the device at the next chunk boundary instead of
  waiting for a phrase to finish, and the samples live in a local byte string
  for the duration of one call - never on disk, never in a buffer that outlives
  the request.

Nothing is imported at application start-up: engine probes use
:func:`importlib.util.find_spec`, and the heavy imports happen inside the voice
worker thread, so a missing or slow engine cannot cost a frame.
"""

from __future__ import annotations

import array
import importlib
import importlib.util
import json
import logging
import math
import os
import sys
import time
from typing import Callable, List, Optional, Tuple

from app.voice.types import (
    SpeechErrorKind,
    SpeechResult,
    SpeechSettings,
    SpeechStatus,
)

logger = logging.getLogger("visioncore.voice.recognizer")

# A 16-bit PCM chunk counts as speech above this RMS. Real speech sits well above
# it (roughly 800-4000) and room tone well below (roughly 30-250), so the gate is
# deliberately generous: a quiet speaker is still captured.
SPEECH_RMS_THRESHOLD = 320.0
# Once speech has been heard, this much silence ends the utterance.
SILENCE_TAIL_SEC = 0.8
# RMS mapped onto a full display bar; used only to draw the *real* level.
LEVEL_FULL_SCALE = 6000.0

CancelCheck = Callable[[], bool]
CaptureCallback = Callable[[], None]
LevelCallback = Callable[[float], None]


class CaptureFailed(Exception):
    """Internal: capture ended without usable audio."""

    def __init__(self, kind: SpeechErrorKind, detail: str = "") -> None:
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail


def _rms(raw: bytes) -> float:
    """Root-mean-square amplitude of 16-bit PCM (measured, never synthesised)."""
    usable = len(raw) - (len(raw) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(raw[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples))


def _display_level(rms: float) -> float:
    return max(0.0, min(1.0, rms / LEVEL_FULL_SCALE))


class SpeechRecognizer:
    """Base class: one microphone, one attempt, one honest result."""

    name = "UNKNOWN"
    detail = ""

    @property
    def available(self) -> bool:
        """True when this engine could plausibly transcribe right now."""
        return True

    @property
    def unavailable_reason(self) -> str:
        """Human-readable reason for :attr:`available` being False."""
        return ""

    def listen(
        self,
        cancel: CancelCheck,
        on_capture_complete: Optional[CaptureCallback] = None,
        on_level: Optional[LevelCallback] = None,
    ) -> SpeechResult:
        """Capture and transcribe one utterance (blocking: worker thread only)."""
        raise NotImplementedError

    def close(self) -> None:
        """Release engine resources. Never blocks on the microphone."""
        return


class NullSpeechRecognizer(SpeechRecognizer):
    """The honest placeholder when no local engine can run on this machine."""

    name = "UNAVAILABLE"

    def __init__(self, reason: str) -> None:
        self._reason = reason or "no local speech engine is available"
        self.detail = self._reason

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    def listen(self, cancel, on_capture_complete=None, on_level=None) -> SpeechResult:
        return SpeechResult.unavailable(SpeechErrorKind.NO_ENGINE, self._reason)


class _SpeechRecognitionEngine(SpeechRecognizer):
    """Shared microphone capture built on the optional ``speech_recognition``.

    Only the offline engines (PocketSphinx, Vosk) are ever used; the package's
    network recognisers are never called, so no code path in VisionCore can
    upload audio.
    """

    def __init__(self, module, settings: SpeechSettings, name: str) -> None:
        self._sr = module
        self.settings = settings
        self.name = name
        self.detail = f"{name} via SpeechRecognition (offline)"

    # -- capture ----------------------------------------------------------- #

    def _microphone(self):
        """Open the input device, translating failures into honest reasons."""
        try:
            return self._sr.Microphone(
                device_index=self.settings.device_index,
                sample_rate=self.settings.sample_rate,
            )
        except AttributeError as exc:
            # The package raises AttributeError when its PyAudio binding is absent.
            raise CaptureFailed(
                SpeechErrorKind.NO_DEVICE,
                "microphone backend missing (PyAudio is not installed)",
            ) from exc
        except OSError as exc:
            message = str(exc).lower()
            if "permission" in message or "access" in message:
                raise CaptureFailed(SpeechErrorKind.PERMISSION, str(exc)) from exc
            raise CaptureFailed(SpeechErrorKind.NO_DEVICE, str(exc) or "no input device") from exc
        except Exception as exc:  # engine-specific failure, reported not raised
            raise CaptureFailed(SpeechErrorKind.FAILED, type(exc).__name__) from exc

    def _capture(
        self,
        cancel: CancelCheck,
        on_capture_complete: Optional[CaptureCallback],
        on_level: Optional[LevelCallback],
    ) -> Tuple[bytes, float, float]:
        """Capture one utterance; returns (raw PCM, seconds, peak level)."""
        settings = self.settings
        chunk_sec = max(0.1, float(settings.chunk_sec))
        limit = max(1.0, float(settings.listen_limit_sec))
        recogniser = self._sr.Recognizer()

        frames: List[bytes] = []
        peak = 0.0
        speech_seen = False
        silence_run = 0.0
        started = time.perf_counter()

        try:
            microphone = self._microphone()
            with microphone as source:
                while True:
                    if cancel():
                        raise CaptureFailed(SpeechErrorKind.CANCELLED)
                    if time.perf_counter() - started >= limit:
                        break
                    chunk = recogniser.record(source, duration=chunk_sec)
                    raw = chunk.get_raw_data() if hasattr(chunk, "get_raw_data") else bytes(chunk)
                    if not raw:
                        continue
                    frames.append(raw)
                    rms = _rms(raw)
                    if rms > peak:
                        peak = rms
                    if on_level is not None:
                        on_level(_display_level(rms))
                    if rms >= SPEECH_RMS_THRESHOLD:
                        speech_seen = True
                        silence_run = 0.0
                    elif speech_seen:
                        silence_run += chunk_sec
                        if silence_run >= SILENCE_TAIL_SEC:
                            break
        except CaptureFailed:
            raise
        except Exception as exc:
            raise CaptureFailed(SpeechErrorKind.FAILED, type(exc).__name__) from exc

        duration = time.perf_counter() - started
        if cancel():
            raise CaptureFailed(SpeechErrorKind.CANCELLED)
        if not speech_seen:
            raise CaptureFailed(
                SpeechErrorKind.TIMEOUT,
                f"no speech within {limit:.0f}s",
            )
        if on_capture_complete is not None:
            on_capture_complete()
        return b"".join(frames), duration, _display_level(peak)

    # -- recognition ------------------------------------------------------- #

    def _transcribe(self, raw: bytes, duration: float, peak: float) -> SpeechResult:
        raise NotImplementedError

    def listen(self, cancel, on_capture_complete=None, on_level=None) -> SpeechResult:
        try:
            raw, duration, peak = self._capture(cancel, on_capture_complete, on_level)
        except CaptureFailed as failure:
            cancelled = failure.kind is SpeechErrorKind.CANCELLED
            if cancelled:
                return SpeechResult.cancelled(failure.detail or "CANCELLED")
            return SpeechResult.failed(failure.kind, failure.detail)
        if cancel():
            return SpeechResult.cancelled()
        return self._transcribe(raw, duration, peak)


class SphinxSpeechRecognizer(_SpeechRecognitionEngine):
    """Offline PocketSphinx recognition (no model download, no network)."""

    def __init__(self, module, settings: SpeechSettings) -> None:
        super().__init__(module, settings, name="POCKETSPHINX")

    def _transcribe(self, raw: bytes, duration: float, peak: float) -> SpeechResult:
        audio = self._sr.AudioData(raw, self.settings.sample_rate, 2)
        recogniser = self._sr.Recognizer()
        try:
            text = recogniser.recognize_sphinx(audio)
        except self._sr.UnknownValueError:
            return SpeechResult.failed(
                SpeechErrorKind.FAILED, "the engine could not transcribe the audio"
            )
        except self._sr.RequestError as exc:
            return SpeechResult.failed(SpeechErrorKind.FAILED, str(exc)[:120])
        except Exception as exc:
            return SpeechResult.failed(SpeechErrorKind.FAILED, type(exc).__name__)
        return _result_from_text(text, duration, peak)


class VoskSpeechRecognizer(_SpeechRecognitionEngine):
    """Streaming offline recognition through Vosk and a local model directory.

    The model is loaded lazily, inside the voice worker thread, because loading a
    Vosk model takes seconds - far too long for the render loop.
    """

    def __init__(self, module, settings: SpeechSettings) -> None:
        super().__init__(module, settings, name="VOSK")
        self._model = None
        self._model_error = ""
        self.detail = f"VOSK (offline, model {os.path.basename(settings.model_path.rstrip('/'))})"

    @property
    def available(self) -> bool:
        """Unavailable once the model has been shown to be unusable."""
        return self._model is not None or not self._model_error

    @property
    def unavailable_reason(self) -> str:
        return self._model_error

    def _load_model(self) -> bool:
        if self._model is not None:
            return True
        try:
            self._model = self._vosk.Model(self.settings.model_path)
        except Exception as exc:
            self._model_error = f"the Vosk model could not be loaded ({type(exc).__name__})"
            logger.warning("Vosk model load failed: %s", type(exc).__name__)
            return False
        return True

    @property
    def _vosk(self):
        return importlib.import_module("vosk")

    def _transcribe(self, raw: bytes, duration: float, peak: float) -> SpeechResult:
        if not self._load_model():
            return SpeechResult.failed(SpeechErrorKind.FAILED, self._model_error)
        try:
            recogniser = self._vosk.KaldiRecognizer(self._model, self.settings.sample_rate)
            recogniser.AcceptWaveform(raw)
            payload = json.loads(recogniser.FinalResult() or "{}")
        except Exception as exc:
            return SpeechResult.failed(SpeechErrorKind.FAILED, type(exc).__name__)
        text = str(payload.get("text", "")).strip()
        confidence = _vosk_confidence(payload)
        return _result_from_text(text, duration, peak, confidence)

    def close(self) -> None:
        self._model = None


def _vosk_confidence(payload: dict) -> Optional[float]:
    """Average per-word confidence reported by Vosk, when it reports one."""
    words = payload.get("result")
    if not isinstance(words, list) or not words:
        return None
    scores = [
        float(word.get("conf"))
        for word in words
        if isinstance(word, dict) and isinstance(word.get("conf"), (int, float))
    ]
    if not scores:
        return None
    return max(0.0, min(1.0, sum(scores) / len(scores)))


def _result_from_text(
    text: str,
    duration: float,
    peak: float,
    confidence: Optional[float] = None,
) -> SpeechResult:
    clean = " ".join((text or "").split())
    if not clean:
        return SpeechResult.failed(
            SpeechErrorKind.FAILED, "the engine returned no transcript"
        )
    return SpeechResult(
        status=SpeechStatus.RESULT,
        text=clean,
        confidence=confidence,
        duration=duration,
        peak_level=peak,
    )


def _module_available(name: str) -> bool:
    """Probe for a module without importing it (start-up cost stays ~zero)."""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # a broken parent package must not break start-up
        return False


# -- engine selection -------------------------------------------------------- #


def _build_vosk(settings: SpeechSettings):
    """Return (recognizer, reason) - exactly one of the two is set."""
    if not _module_available("vosk"):
        return None, "the vosk package is not installed"
    if not settings.model_path:
        return None, "vosk is installed but VISIONCORE_SPEECH_MODEL is not set"
    if not os.path.isdir(settings.model_path):
        return None, "the configured Vosk model directory does not exist"
    module = importlib.import_module("vosk")
    return VoskSpeechRecognizer(module, settings), ""


def _build_sphinx(settings: SpeechSettings):
    if not _module_available("speech_recognition"):
        return None, "the SpeechRecognition package is not installed"
    if not _module_available("pocketsphinx"):
        return None, "the offline PocketSphinx engine (pocketsphinx) is not installed"
    module = importlib.import_module("speech_recognition")
    return SphinxSpeechRecognizer(module, settings), ""


_BUILDERS = {"vosk": _build_vosk, "sphinx": _build_sphinx}
# Preference order: a Vosk model is the better transcriber, PocketSphinx needs no
# model download at all.
_AUTO_ORDER = ("vosk", "sphinx")


def create_recognizer(settings: SpeechSettings) -> SpeechRecognizer:
    """Build the best *local* recogniser for this machine (never raises).

    With ``engine=auto`` the first usable engine wins; if none is usable the
    result is a :class:`NullSpeechRecognizer` carrying the reason each candidate
    failed, which the HUD reports as ``VOICE UNAVAILABLE``.
    """
    if not settings.enabled:
        return NullSpeechRecognizer("voice input is disabled in configuration")

    order = _AUTO_ORDER if settings.engine == "auto" else (settings.engine,)
    reasons: List[str] = []
    for candidate in order:
        builder = _BUILDERS.get(candidate)
        if builder is None:
            reasons.append(f"unknown engine {candidate!r}")
            continue
        try:
            recognizer, reason = builder(settings)
        except Exception as exc:  # a broken optional package is a reason, not a crash
            recognizer, reason = None, f"{candidate} could not be initialised ({type(exc).__name__})"
        if recognizer is not None:
            logger.info("Voice engine ready: %s", recognizer.detail)
            return recognizer
        reasons.append(reason)

    summary = "; ".join(reason for reason in reasons if reason)
    if not summary:
        summary = "no local speech engine is available"
    logger.info("Voice input unavailable: %s", summary)
    return NullSpeechRecognizer(summary)
