"""Background request client for the assistant.

A provider call can take seconds. It runs on one daemon worker thread and hands
its result back through a queue that the render loop drains without ever
blocking, so a slow or unreachable provider cannot cost a single frame. Nothing
here touches the camera, the gesture pipeline or the interface: it takes an
:class:`~app.ai.types.AIRequest` and produces an
:class:`~app.ai.types.AIResult`.

One request is in flight at a time. A second request is refused while one is
pending, and cancelling marks the generation so that an answer which arrives
late is reported as cancelled and never enters the conversation.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.ai.parser import parse_reply
from app.ai.provider import AIProvider, ProviderError
from app.ai.types import AIErrorKind, AIRequest, AIResult

logger = logging.getLogger("visioncore.ai.client")

POLL_INTERVAL_SEC = 0.20
CLOSE_TIMEOUT_SEC = 0.50
_STOP = object()


@dataclass(frozen=True, slots=True)
class _Job:
    """One queued request plus the generation it belongs to."""

    request: AIRequest
    generation: int


class AIClient:
    """Runs provider calls off the render thread and collects their results."""

    def __init__(self, provider: AIProvider) -> None:
        self.provider = provider
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._results: "queue.Queue[AIResult]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._generation = 0
        self._in_flight = False
        self._closed = False
        self.sent = 0
        self.completed = 0
        self.cancelled = 0
        self.failed = 0

    # -- introspection ----------------------------------------------------- #

    @property
    def in_flight(self) -> bool:
        """True while a request is queued or being answered."""
        with self._lock:
            return self._in_flight

    @property
    def running(self) -> bool:
        """True while the worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # -- api --------------------------------------------------------------- #

    def submit(self, request: AIRequest) -> bool:
        """Queue one request. Returns False when one is already pending."""
        if self._closed or not self.provider.available:
            return False
        with self._lock:
            if self._in_flight:
                return False
            self._in_flight = True
            generation = self._generation
        self._ensure_worker()
        self.sent += 1
        self._queue.put(_Job(request=request, generation=generation))
        return True

    def poll(self) -> Tuple[AIResult, ...]:
        """Collect every result that arrived since the last call (never blocks)."""
        results: List[AIResult] = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                break
        return tuple(results)

    def cancel_all(self) -> int:
        """Abandon everything queued or in flight; late answers are ignored."""
        with self._lock:
            self._generation += 1
            had_work = self._in_flight
            self._in_flight = False
        if had_work:
            self.cancelled += 1
        drained = 0
        while True:
            try:
                self._queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if had_work or drained:
            logger.info("AI requests cancelled (%d queued)", drained)
        return drained

    def close(self) -> None:
        """Stop the worker. Never waits on the network for longer than a moment."""
        self._closed = True
        self.cancel_all()
        thread = self._thread
        if thread is None:
            return
        self._queue.put(_STOP)
        thread.join(CLOSE_TIMEOUT_SEC)
        if thread.is_alive():
            # A provider call is still blocked in the socket. The worker is a
            # daemon, so it cannot hold the process open; the result is dropped.
            logger.info("AI client closed with a request still in flight")
        self._thread = None

    # -- worker ------------------------------------------------------------ #

    def _ensure_worker(self) -> None:
        if self.running or self._closed:
            return
        self._thread = threading.Thread(
            target=self._run, name="visioncore-ai", daemon=True
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
        request = job.request
        with self._lock:
            stale = job.generation != self._generation
        if stale:
            self._results.put(
                AIResult(request_id=request.request_id, cancelled=True, detail="CANCELLED")
            )
            return

        try:
            reply = self.provider.chat(request.messages, request.context)
        except ProviderError as exc:
            self.failed += 1
            self._results.put(
                AIResult(request_id=request.request_id, error=exc.kind, detail=exc.detail)
            )
        except Exception as exc:  # never let a provider bug take the app down
            self.failed += 1
            logger.warning("AI provider raised %s", type(exc).__name__)
            self._results.put(
                AIResult(
                    request_id=request.request_id,
                    error=AIErrorKind.INTERNAL,
                    detail=type(exc).__name__,
                )
            )
        else:
            self.completed += 1
            # Transport succeeded; the answer is read as data here, on the worker
            # thread, so the render loop only ever sees a typed result.
            parsed = parse_reply(reply.text)
            self._results.put(
                AIResult(
                    request_id=request.request_id,
                    reply=parsed.reply,
                    error=parsed.error,
                    detail=parsed.detail,
                )
            )
        finally:
            with self._lock:
                self._in_flight = False
