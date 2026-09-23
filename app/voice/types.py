"""Typed vocabulary for the Phase 8 voice input layer.

Voice is an *input method*, not a second assistant: a transcript is text, and
text goes to the same assistant, the same parser, the same allowlist and the
same safety gates that a typed message uses. This module only describes what the
microphone is doing and what a recogniser returned - it holds no audio, opens no
device and never decides anything about control.

Two vocabularies live here on purpose:

* :class:`VoiceState` is what the interface shows (``OFF``, ``LISTENING``,
  ``PROCESSING``, ``READY``, ``ERROR``, ``UNAVAILABLE``);
* :class:`SpeechStatus` is what a recogniser reports for one attempt, including
  the two outcomes that must never be confused with an empty transcript
  (``CANCELLED`` and an error kind).

Nothing in this package records audio. A capture exists only between an explicit
activation and the moment the recogniser finishes with it, and the samples are
held in memory for that call only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VoiceState(str, Enum):
    """Microphone state as shown on the HUD.

    ``OFF`` is the default and the resting state: the microphone is not open.
    ``LISTENING`` means the device is open and capturing right now, which is the
    only state in which the interface may say the microphone is on.
    """

    UNAVAILABLE = "UNAVAILABLE"
    OFF = "OFF"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    ERROR = "ERROR"

    @property
    def label(self) -> str:
        return self.value

    @property
    def active(self) -> bool:
        """True while the microphone is open or audio is being transcribed."""
        return self in (VoiceState.LISTENING, VoiceState.PROCESSING)

    @property
    def listening(self) -> bool:
        return self is VoiceState.LISTENING


class SpeechStatus(str, Enum):
    """Outcome of one speech recognition attempt (never a fake transcript)."""

    UNAVAILABLE = "UNAVAILABLE"   # no engine and/or no usable input device
    IDLE = "IDLE"                 # nothing attempted yet
    LISTENING = "LISTENING"       # audio is being captured
    PROCESSING = "PROCESSING"     # audio captured, engine is transcribing
    RESULT = "RESULT"             # a transcript was produced
    ERROR = "ERROR"               # the attempt failed
    CANCELLED = "CANCELLED"       # the user cancelled; the result is discarded


class SpeechErrorKind(str, Enum):
    """Why a recognition attempt did not produce a transcript."""

    NO_ENGINE = "NO_ENGINE"       # no speech engine is installed
    NO_DEVICE = "NO_DEVICE"       # no microphone/input device is available
    PERMISSION = "PERMISSION"     # the operating system refused the microphone
    TIMEOUT = "TIMEOUT"           # the listening window expired without speech
    FAILED = "FAILED"             # the engine itself failed
    CANCELLED = "CANCELLED"       # cancelled by the user

    @property
    def label(self) -> str:
        return {
            SpeechErrorKind.NO_ENGINE: "SPEECH UNAVAILABLE: NO ENGINE",
            SpeechErrorKind.NO_DEVICE: "SPEECH UNAVAILABLE: NO MICROPHONE",
            SpeechErrorKind.PERMISSION: "SPEECH MICROPHONE PERMISSION DENIED",
            SpeechErrorKind.TIMEOUT: "LISTENING TIMEOUT",
            SpeechErrorKind.FAILED: "SPEECH RECOGNITION FAILED",
            SpeechErrorKind.CANCELLED: "SPEECH CANCELLED",
        }[self]

    @property
    def unavailable(self) -> bool:
        """True when the host cannot provide a working microphone at all."""
        return self in (SpeechErrorKind.NO_ENGINE, SpeechErrorKind.NO_DEVICE,
                        SpeechErrorKind.PERMISSION)


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """The honest outcome of one recognition attempt.

    ``text`` is only ever populated when ``status`` is ``RESULT``, so a caller
    cannot accidentally treat an empty attempt as an empty transcript.
    """

    status: SpeechStatus = SpeechStatus.IDLE
    text: str = ""
    confidence: Optional[float] = None
    error: Optional[SpeechErrorKind] = None
    detail: str = ""
    duration: float = 0.0
    peak_level: Optional[float] = None

    @property
    def ok(self) -> bool:
        """True only for a real transcript."""
        return self.status is SpeechStatus.RESULT and bool(self.text.strip())

    @property
    def error_label(self) -> str:
        return self.error.label if self.error is not None else self.status.value

    @classmethod
    def unavailable(cls, error: SpeechErrorKind, detail: str = "") -> "SpeechResult":
        return cls(status=SpeechStatus.UNAVAILABLE, error=error, detail=detail)

    @classmethod
    def cancelled(cls, detail: str = "CANCELLED") -> "SpeechResult":
        return cls(status=SpeechStatus.CANCELLED, error=SpeechErrorKind.CANCELLED,
                   detail=detail)

    @classmethod
    def failed(cls, error: SpeechErrorKind, detail: str = "") -> "SpeechResult":
        return cls(status=SpeechStatus.ERROR, error=error, detail=detail)


@dataclass(frozen=True, slots=True)
class SpeechSettings:
    """Resolved voice configuration (environment only, no secret).

    Every provider in this package is offline: audio is captured locally and
    transcribed locally. There is deliberately no cloud recogniser option, so
    enabling voice can never turn into an upload path.
    """

    engine: str = "auto"                 # auto | vosk | sphinx | none
    model_path: str = ""                 # offline model directory (vosk)
    device_index: Optional[int] = None   # input device override
    sample_rate: int = 16000
    listen_limit_sec: float = 8.0        # how long the microphone may stay open
    phrase_limit_sec: float = 15.0       # longest single utterance accepted
    chunk_sec: float = 0.5               # capture granularity (cancellation step)

    @property
    def enabled(self) -> bool:
        """False when voice input is switched off in configuration."""
        return self.engine != "none"


@dataclass(frozen=True, slots=True)
class VoiceSnapshot:
    """Immutable view of the voice layer for one frame."""

    state: VoiceState = VoiceState.OFF
    engine: str = "NONE"
    # True while voice input is usable: an engine exists and no activation has
    # ended in "the microphone is not usable" (an unplugged device, a denied
    # permission, or no engine at all).
    available: bool = False
    detail: str = ""
    note: str = ""
    listening_seconds: float = 0.0
    limit_seconds: float = 0.0
    level: Optional[float] = None
    transcript: str = ""
    confidence: Optional[float] = None
    last_error: Optional[SpeechErrorKind] = None
    sessions: int = 0
    transcripts: int = 0
    cancellations: int = 0
    errors: int = 0

    @property
    def status_label(self) -> str:
        return self.state.label

    @property
    def active(self) -> bool:
        return self.state.active

    @property
    def listening(self) -> bool:
        return self.state.listening

    @property
    def remaining_seconds(self) -> float:
        """How long the microphone will stay open if nothing is said."""
        if not self.state.listening or self.limit_seconds <= 0:
            return 0.0
        return max(0.0, self.limit_seconds - self.listening_seconds)
