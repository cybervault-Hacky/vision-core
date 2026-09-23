"""Voice activation, cancellation and the honest state the HUD shows.

The microphone is closed until the user asks for it, and the render loop never
touches it. This controller owns:

* **deliberate activation** - ``start()`` only ever runs because a key was
  pressed or a button was clicked, never because a hand appeared, the AI panel
  opened, the camera started or the application launched;
* **one background worker** - capture and transcription run on a daemon thread,
  and the render loop only drains a queue (never blocking, so a slow engine costs
  the camera nothing);
* **a bounded window** - listening ends by itself at
  :attr:`~app.voice.types.SpeechSettings.listen_limit_sec`, and a watchdog
  reports ``LISTENING TIMEOUT`` even if an engine ignores its own deadline;
* **immediate cancellation** - cancelling flips the visible state to ``OFF`` at
  once, sets the cancel flag, and bumps a generation counter so a late result is
  discarded rather than delivered. A cancelled transcript can never execute.

State is only ever what really happened: ``LISTENING`` means the device is open,
``PROCESSING`` means audio was captured and is being transcribed, and an engine
that is missing or has no microphone reports ``UNAVAILABLE`` with its reason.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from app.voice.recognizer import (
    NullSpeechRecognizer,
    SpeechRecognizer,
    create_recognizer,
)
from app.voice.types import (
    SpeechErrorKind,
    SpeechResult,
    SpeechSettings,
    SpeechStatus,
    VoiceSnapshot,
    VoiceState,
)

logger = logging.getLogger("visioncore.voice.controller")

POLL_INTERVAL_SEC = 0.20
CLOSE_TIMEOUT_SEC = 0.80
# How long READY ("a transcript just arrived") and ERROR stay on screen.
READY_HOLD_SEC = 2.5
ERROR_HOLD_SEC = 4.0
# Grace period before the watchdog overrules an engine that ignores its deadline.
WATCHDOG_GRACE_SEC = 3.0

_STOP = object()


@dataclass(frozen=True, slots=True)
class _Job:
    """One listening attempt plus the activation it belongs to."""

    generation: int
    cancel: threading.Event


class VoiceController:
    """Owns microphone activation state; the application owns what happens next."""

    def __init__(
        self,
        settings: SpeechSettings,
        recognizer: Optional[SpeechRecognizer] = None,
        clock: Optional[Callable[[], float]] = None,
        notify: Optional[Callable[[str, bool, str], None]] = None,
    ) -> None:
        self.settings = settings
        self.recognizer = recognizer if recognizer is not None else create_recognizer(settings)
        self._clock = clock or time.perf_counter
        self._notify = notify

        self._queue: "queue.Queue[object]" = queue.Queue()
        self._results: "queue.Queue[Tuple[SpeechResult, int]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._generation = 0
        self._cancel = threading.Event()
        self._closed = False

        available = self.recognizer.available
        self._state = VoiceState.OFF if available else VoiceState.UNAVAILABLE
        self._detail = self.recognizer.detail or self.recognizer.name
        self._note = "" if available else self.recognizer.unavailable_reason
        self._started_at = 0.0
        self._limit = settings.listen_limit_sec
        self._level: Optional[float] = None
        self._transcript = ""
        self._confidence: Optional[float] = None
        self._last_error: Optional[SpeechErrorKind] = None
        self._hold_until = 0.0
        self._pending: Optional[SpeechResult] = None

        self.sessions = 0
        self.transcripts = 0
        self.cancellations = 0
        self.errors = 0

    # -- introspection ----------------------------------------------------- #

    @property
    def available(self) -> bool:
        """True when a local engine could listen right now."""
        return self.recognizer.available

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def active(self) -> bool:
        """True while the microphone is open or audio is being transcribed."""
        return self._state.active

    @property
    def listening(self) -> bool:
        return self._state.listening

    @property
    def running(self) -> bool:
        """True while the voice worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def engine_name(self) -> str:
        return self.recognizer.name

    def snapshot(self) -> VoiceSnapshot:
        """Immutable view for one frame."""
        with self._lock:
            listening_seconds = (
                max(0.0, self._clock() - self._started_at)
                if self._state.listening
                else 0.0
            )
            return VoiceSnapshot(
                state=self._state,
                engine=self.recognizer.name,
                available=self._state is not VoiceState.UNAVAILABLE,
                detail=self._detail,
                note=self._note,
                listening_seconds=listening_seconds,
                limit_seconds=self._limit,
                level=self._level if self._state.listening else None,
                transcript=self._transcript,
                confidence=self._confidence,
                last_error=self._last_error,
                sessions=self.sessions,
                transcripts=self.transcripts,
                cancellations=self.cancellations,
                errors=self.errors,
            )

    # -- activation -------------------------------------------------------- #

    def toggle(self) -> bool:
        """Start listening, or cancel it when it is already running.

        Returns True when the microphone is now open, so the interface can make
        the activation visible.
        """
        if self.active:
            self.cancel()
            return False
        return self.start()

    def start(self) -> bool:
        """Open the microphone for one bounded listening window."""
        if self._closed or self.active:
            return False

        if not self.recognizer.available:
            # No engine installed is a different problem from an engine that
            # cannot open a device, and the interface says which one it is.
            kind = (
                SpeechErrorKind.NO_ENGINE
                if isinstance(self.recognizer, NullSpeechRecognizer)
                else SpeechErrorKind.FAILED
            )
            self._fail_now(kind, self.recognizer.unavailable_reason or "no microphone is available")
            return False

        with self._lock:
            self._generation += 1
            generation = self._generation
            cancel = threading.Event()
            self._cancel = cancel
            self._started_at = self._clock()
            self._limit = self.settings.listen_limit_sec
            self._level = None
            self._set_state(VoiceState.LISTENING, "")

        self._ensure_worker()
        self.sessions += 1
        self._queue.put(_Job(generation=generation, cancel=cancel))
        logger.info("Voice listening started (%s)", self.recognizer.name)
        return True

    def cancel(self, detail: str = "CANCELLED", label: str = "VOICE CANCELLED") -> bool:
        """Stop capturing at once; any late transcript is discarded."""
        with self._lock:
            if not self._state.active and self._pending is None:
                return False
            self._generation += 1
            self._cancel.set()
            self._level = None
            self._pending = None
            self._transcript = ""
            self._confidence = None
            self._set_state(VoiceState.OFF, detail)
        self.cancellations += 1
        self._note_event(label, False, "MIC OFF")
        logger.info("Voice capture closed (%s)", detail)
        return True

    def close(self) -> None:
        """Cancel, stop the worker and release the engine. Never blocks long."""
        self._closed = True
        with self._lock:
            self._generation += 1
            self._cancel.set()
            self._pending = None
            self._level = None
            self._set_state(VoiceState.OFF, "")

        thread = self._thread
        if thread is not None:
            self._queue.put(_STOP)
            thread.join(CLOSE_TIMEOUT_SEC)
            if thread.is_alive():
                logger.info("Voice worker still finishing a capture at shutdown")
            self._thread = None
        try:
            self.recognizer.close()
        except Exception as exc:
            logger.warning("Error releasing the speech engine: %s", type(exc).__name__)

    # -- per frame --------------------------------------------------------- #

    def update(self, now: Optional[float] = None) -> None:
        """Drain results and advance the visible state. Never blocks."""
        now = self._clock() if now is None else now

        while True:
            try:
                result, generation = self._results.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                stale = generation != self._generation
            if stale:
                # Cancelled or superseded: the transcript is dropped, never sent.
                logger.debug("Discarded a stale speech result")
                continue
            self._consume(result, now)

        if self._state.listening and now - self._started_at > (
            self._limit + WATCHDOG_GRACE_SEC
        ):
            # An engine that ignores its own deadline must not hold the
            # microphone open: report the timeout and close the device.
            logger.warning("Voice watchdog closed a capture that overran its window")
            self.cancel("LISTENING TIMEOUT", label=SpeechErrorKind.TIMEOUT.label)

        if self._state in (VoiceState.READY, VoiceState.ERROR) and now >= self._hold_until:
            with self._lock:
                if self._state in (VoiceState.READY, VoiceState.ERROR):
                    self._set_state(VoiceState.OFF, self._note)

    def take_transcript(self) -> Optional[SpeechResult]:
        """Hand the newest transcript to the application exactly once."""
        with self._lock:
            result = self._pending
            self._pending = None
        return result

    # -- worker ------------------------------------------------------------ #

    def _ensure_worker(self) -> None:
        if self.running or self._closed:
            return
        self._thread = threading.Thread(
            target=self._run, name="visioncore-voice", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=POLL_INTERVAL_SEC)
            except queue.Empty:
                continue
            if job is _STOP:
                return
            self._handle(job)

    def _handle(self, job: _Job) -> None:
        with self._lock:
            stale = job.generation != self._generation
        if stale:
            self._results.put((SpeechResult.cancelled("CANCELLED"), job.generation))
            return

        try:
            result = self.recognizer.listen(
                cancel=job.cancel.is_set,
                on_capture_complete=lambda: self._capture_complete(job.generation),
                on_level=lambda level: self._publish_level(job.generation, level),
            )
        except Exception as exc:  # an engine bug must never take the app down
            logger.warning("Speech engine raised %s", type(exc).__name__)
            result = SpeechResult.failed(SpeechErrorKind.FAILED, type(exc).__name__)
        self._results.put((result, job.generation))

    def _capture_complete(self, generation: int) -> None:
        """Called from the worker the moment audio has been captured."""
        with self._lock:
            if generation != self._generation:
                return
            self._set_state(VoiceState.PROCESSING, "TRANSCRIBING")
        self._note_event("SPEECH CAPTURED", True, "TRANSCRIBING")

    def _publish_level(self, generation: int, level: float) -> None:
        """Publish a *measured* audio level for the listening indicator."""
        with self._lock:
            if generation != self._generation:
                return
            self._level = max(0.0, min(1.0, float(level)))

    # -- results ----------------------------------------------------------- #

    def _consume(self, result: SpeechResult, now: float) -> None:
        """Turn one recogniser outcome into the visible state."""
        if result.ok:
            self.transcripts += 1
            self._last_error = None
            with self._lock:
                self._transcript = result.text
                self._confidence = result.confidence
                self._pending = result
                self._level = None
                self._hold_until = now + READY_HOLD_SEC
                self._set_state(VoiceState.READY, f'HEARD: "{_shorten(result.text)}"')
            logger.info("Voice transcript received (%d characters)", len(result.text))
            return

        if result.status is SpeechStatus.CANCELLED:
            with self._lock:
                self._level = None
                if self._state.active:
                    self._set_state(VoiceState.OFF, "CANCELLED")
            return

        self.errors += 1
        self._last_error = result.error
        label = result.error_label
        detail = result.detail or ""

        if result.status is SpeechStatus.UNAVAILABLE or (
            result.error is not None and result.error.unavailable
        ):
            with self._lock:
                self._level = None
                self._set_state(VoiceState.UNAVAILABLE, _join(label, detail))
            self._note_event("VOICE UNAVAILABLE", False, detail or label)
            return

        if result.error is SpeechErrorKind.TIMEOUT:
            # No speech inside the window: the microphone closes by itself.
            with self._lock:
                self._level = None
                self._set_state(VoiceState.OFF, label)
            self._note_event(label, False, "MIC OFF")
            return

        with self._lock:
            self._level = None
            self._hold_until = now + ERROR_HOLD_SEC
            self._set_state(VoiceState.ERROR, _join(label, detail))
        self._note_event(label, False, detail)

    def _fail_now(self, kind: SpeechErrorKind, detail: str) -> None:
        """Report an activation attempt that could not even open a device."""
        self.errors += 1
        self._last_error = kind
        with self._lock:
            self._set_state(VoiceState.UNAVAILABLE, _join(kind.label, detail))
        self._note_event("VOICE UNAVAILABLE", False, detail or kind.label)
        logger.info("Voice activation refused: %s", detail or kind.label)

    # -- helpers ----------------------------------------------------------- #

    def _set_state(self, state: VoiceState, note: str) -> None:
        """Set the visible state (call with the lock held)."""
        self._state = state
        if note:
            self._note = note

    def _note_event(self, label: str, success: bool, detail: str = "") -> None:
        if self._notify is not None:
            self._notify(label, success, detail)


def _join(label: str, detail: str) -> str:
    return f"{label} / {detail}" if detail else label


def _shorten(text: str, limit: int = 48) -> str:
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "..."
