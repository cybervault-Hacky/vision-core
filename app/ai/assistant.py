"""The VisionCore AI assistant: conversation, status and action hand-off.

The assistant is a coordinator, not a controller. It

* keeps the conversation in memory (never on disk),
* asks a provider only when the user actually sends a message,
* hands at most one allowlisted action to :class:`~app.ai.router.ActionRouter`,
  which is the layer that reaches the existing controllers through the existing
  intent router and safety gates,
* and reports exactly what happened, including refusals.

Status is never cosmetic. ``THINKING`` is only entered while a request is really
with a provider, a local answer never claims to be a model reply, and every
error keeps the provider's own failure kind so the interface can be honest about
what failed.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional, Tuple

from app.ai.client import AIClient
from app.ai.context import AIContext
from app.ai.local import answer as local_answer
from app.ai.local import off_topic_answer, provider_note
from app.ai.parser import parse_local
from app.ai.provider import AIProvider, create_provider
from app.ai.router import (
    ActionRouter,
    UNAVAILABLE_SENTENCE,
    normalise,
    requests_system_change,
)
from app.ai.types import (
    AIActionOutcome,
    AIActionPlan,
    AIErrorKind,
    AIRequest,
    AIResult,
    AISettings,
    AISnapshot,
    AIStatus,
    ChatMessage,
    ChatRole,
    ResponseSource,
)

logger = logging.getLogger("visioncore.ai.assistant")

# Longest message accepted from the user (characters).
MAX_MESSAGE_CHARS = 600
# How long a proposed action waits for confirmation before it is dropped.
CONFIRM_WINDOW_SEC = 25.0
# Minimum time a transient status stays visible, so it cannot flicker.
RESPOND_HOLD_SEC = 0.45
EXECUTE_HOLD_SEC = 0.55
# Watchdog: a provider that never answers is reported as a timeout.
TIMEOUT_FACTOR = 1.6


class AIAssistant:
    """Owns the conversation, the background client and the action hand-off."""

    def __init__(
        self,
        settings: AISettings,
        router: Optional[ActionRouter] = None,
        context_provider: Optional[Callable[[], AIContext]] = None,
        notify: Optional[Callable[[str, bool, str], None]] = None,
        provider: Optional[AIProvider] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.settings = settings
        self.provider = provider if provider is not None else create_provider(settings)
        self.client = AIClient(self.provider)
        self.router = router
        self._context_provider = context_provider or AIContext
        self._notify = notify
        self._clock = clock or time.perf_counter

        self._messages: List[ChatMessage] = []
        self._pending: Optional[AIActionPlan] = None
        self._pending_since = 0.0
        self._request_id = 0
        self._active_request = 0
        self._request_since = 0.0
        self._in_flight = False
        self._busy_until = 0.0
        self._status = AIStatus.READY if settings.configured else AIStatus.NOT_CONFIGURED
        self._error: Optional[AIErrorKind] = None
        self._error_detail = ""
        self.turns = 0

    # -- introspection ----------------------------------------------------- #

    @property
    def configured(self) -> bool:
        """True when the assistant has a provider it can actually call.

        This is the provider's own verdict, so the status shown to the user can
        never claim to be configured when nothing is behind it (and a test can
        attach a provider without pretending to own a credential).
        """
        return bool(self.provider.available)

    @property
    def provider_available(self) -> bool:
        return self.provider.available

    @property
    def status(self) -> AIStatus:
        return self._status

    @property
    def messages(self) -> Tuple[ChatMessage, ...]:
        return tuple(self._messages)

    @property
    def pending(self) -> Optional[AIActionPlan]:
        return self._pending

    @property
    def note(self) -> str:
        """One-line description of what the panel can currently do."""
        return provider_note(self.configured)

    def snapshot(self) -> AISnapshot:
        """Immutable view for the interface."""
        now = self._clock()
        return AISnapshot(
            status=self._status,
            provider=self.provider.name,
            model=self.settings.model or "",
            configured=self.configured,
            messages=tuple(self._messages),
            pending=self._pending,
            pending_seconds=(now - self._pending_since) if self._pending else 0.0,
            error=self._error,
            error_detail=self._error_detail,
            note=self.note,
            in_flight=self._in_flight,
            turns=self.turns,
        )

    # -- conversation ------------------------------------------------------ #

    def send(self, text: str) -> bool:
        """Send one typed message. Never blocks and never calls the network directly."""
        message = (text or "").strip()
        # Only a request that is genuinely with a provider blocks another message;
        # the brief RESPONDING/EXECUTING status never swallows typed input.
        if not message or self._in_flight or self._status in (
            AIStatus.THINKING,
            AIStatus.EXECUTING,
        ):
            return False
        message = message[:MAX_MESSAGE_CHARS]
        now = self._clock()

        self._cancel_pending(note="SUPERSEDED")
        self._append(ChatRole.USER, message, now, ResponseSource.SYSTEM)
        self.turns += 1

        # Some requests are simply not available through VisionCore, and that
        # answer is fixed: it is decided here, not left to a model.
        if requests_system_change(message):
            self._append(
                ChatRole.ASSISTANT,
                UNAVAILABLE_SENTENCE,
                now,
                ResponseSource.SYSTEM,
                detail="NOT AVAILABLE",
            )
            self._error = None
            self._error_detail = ""
            self._hold(AIStatus.RESPONDING, now)
            return True

        if not self.provider_available:
            self._answer_locally(message, now)
            return True

        request = AIRequest(
            request_id=self._next_request_id(),
            messages=tuple(self._history()),
            context=self._context().prompt_text(),
            timestamp=now,
        )
        if not self.client.submit(request):
            self._append(
                ChatRole.SYSTEM,
                "A request is already in flight; wait for it to finish.",
                now,
                ResponseSource.SYSTEM,
            )
            return False

        self._active_request = request.request_id
        self._request_since = now
        self._in_flight = True
        self._error = None
        self._error_detail = ""
        self._set_status(AIStatus.THINKING, now)
        return True

    def update(self, now: Optional[float] = None) -> None:
        """Drain results and advance the status. Called once per frame."""
        now = self._clock() if now is None else now

        for result in self.client.poll():
            self._consume(result, now)

        if self._in_flight and now - self._request_since > (
            self.settings.timeout_sec * TIMEOUT_FACTOR
        ):
            # The socket timeout should have fired first; this is the safety net.
            self._fail(AIErrorKind.TIMEOUT, "No answer in time", now, fallback=True)

        if self._pending is not None and now - self._pending_since > CONFIRM_WINDOW_SEC:
            self._cancel_pending(note="EXPIRED")

        if self._status in (AIStatus.RESPONDING, AIStatus.EXECUTING, AIStatus.THINKING):
            if not self._in_flight and now >= self._busy_until:
                self._set_status(self._base_status(), now)

    def clear(self) -> None:
        """Drop the conversation and any pending work (memory only anyway)."""
        self.client.cancel_all()
        self._in_flight = False
        self._pending = None
        self._messages.clear()
        self._error = None
        self._error_detail = ""
        self._status = self._base_status()
        logger.info("AI conversation cleared")

    def close(self) -> None:
        """Shut down cleanly: cancel everything and discard the history."""
        self.client.cancel_all()
        self.client.close()
        self._in_flight = False
        self._pending = None
        self._messages.clear()
        logger.info(
            "AI assistant stopped (sent %d, answered %d, failed %d)",
            self.client.sent,
            self.client.completed,
            self.client.failed,
        )

    # -- confirmation ------------------------------------------------------ #

    def confirm(self) -> bool:
        """Run the action that is waiting for confirmation."""
        plan = self._pending
        if plan is None:
            return False
        now = self._clock()
        self._pending = None
        self._execute(plan, now)
        return True

    def cancel(self) -> bool:
        """Drop the action that is waiting for confirmation."""
        if self._pending is None:
            return False
        self._cancel_pending(note="CANCELLED")
        return True

    # -- internals --------------------------------------------------------- #

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _history(self) -> List[ChatMessage]:
        """The tail of the conversation, for the provider only."""
        keep = max(2, int(self.settings.context_messages))
        return self._messages[-keep:]

    def _context(self) -> AIContext:
        return self._context_provider()

    def _append(
        self,
        role: ChatRole,
        content: str,
        now: float,
        source: ResponseSource,
        detail: str = "",
    ) -> ChatMessage:
        message = ChatMessage(
            role=role, content=content, timestamp=now, source=source, detail=detail
        )
        self._messages.append(message)
        return message

    def _base_status(self) -> AIStatus:
        if not self.configured:
            return AIStatus.NOT_CONFIGURED
        if self._error is not None:
            return AIStatus.ERROR
        return AIStatus.READY

    def _set_status(self, status: AIStatus, now: float) -> None:
        self._status = status
        if status in (AIStatus.RESPONDING, AIStatus.EXECUTING):
            self._busy_until = now + (
                RESPOND_HOLD_SEC if status is AIStatus.RESPONDING else EXECUTE_HOLD_SEC
            )

    def _hold(self, status: AIStatus, now: float) -> None:
        """Show a transient status that returns to the base state by itself."""
        self._set_status(status, now)

    def _answer_locally(self, message: str, now: float) -> None:
        """Deterministic answer path: used when no provider can answer."""
        context = self._context()
        result = local_answer(message, context)
        text = result.text if result is not None else off_topic_answer()
        reply = parse_local(text)
        self._append(
            ChatRole.ASSISTANT, reply.text, now, reply.source, detail="LOCAL"
        )
        self._hold(AIStatus.RESPONDING, now)

    def _consume(self, result: AIResult, now: float) -> None:
        """Handle one background result."""
        if result.request_id != self._active_request:
            return
        self._in_flight = False

        if result.cancelled:
            logger.info("AI request cancelled")
            self._set_status(self._base_status(), now)
            return

        if result.reply is None:
            self._fail(
                result.error or AIErrorKind.INTERNAL,
                result.detail,
                now,
                fallback=True,
            )
            return

        self._error = None
        self._error_detail = ""
        self._set_status(AIStatus.RESPONDING, now)
        reply = result.reply
        self._append(ChatRole.ASSISTANT, reply.text, now, reply.source)

        if result.error is AIErrorKind.REJECTED:
            # The answer was fine, the requested action was not: say so plainly
            # and do not run anything.
            self._append(
                ChatRole.SYSTEM,
                f"ACTION REFUSED: {result.detail or 'not in the allowlist'}",
                now,
                ResponseSource.SYSTEM,
                detail="REFUSED",
            )
            self._note(f"{self._last_user_label()} REFUSED", False, result.detail)
            return

        if reply.action is None:
            return

        spec, reason = _normalise_plan(reply.action)
        if spec is None:
            self._append(
                ChatRole.SYSTEM,
                f"ACTION REFUSED: {reason}",
                now,
                ResponseSource.SYSTEM,
                detail="REFUSED",
            )
            self._note(f"{reply.action.label} REFUSED", False, reason)
            return

        if spec.requires_confirmation:
            self._pending = spec
            self._pending_since = now
            self._append(
                ChatRole.SYSTEM,
                f"CONFIRM REQUIRED: {spec.label}",
                now,
                ResponseSource.SYSTEM,
                detail="CONFIRM",
            )
            return

        self._execute(spec, now)

    def _execute(self, plan: AIActionPlan, now: float) -> None:
        """Hand one allowlisted plan to the action router and report the truth."""
        if self.router is None:
            self._append(
                ChatRole.SYSTEM,
                "ACTION NOT AVAILABLE: no action route is attached",
                now,
                ResponseSource.SYSTEM,
                detail="NO ROUTE",
            )
            return

        self._set_status(AIStatus.EXECUTING, now)
        outcome = self.router.route(plan, self._context())
        self._append(
            ChatRole.SYSTEM,
            _outcome_text(outcome),
            now,
            ResponseSource.SYSTEM,
            detail="RESULT",
        )
        if not outcome.accepted:
            # Nothing reached a controller, so no controller notification will
            # appear: report the refusal through the same feedback system.
            self._note(f"{plan.label} FAILED", False, outcome.detail)

    def _note(self, label: str, success: bool, detail: str = "") -> None:
        if self._notify is not None:
            self._notify(label, success, detail)

    def _last_user_label(self) -> str:
        for message in reversed(self._messages):
            if message.role is ChatRole.USER:
                return message.content[:40].upper()
        return "REQUEST"

    def _cancel_pending(self, note: str) -> None:
        if self._pending is None:
            return
        plan = self._pending
        self._pending = None
        now = self._clock()
        text = (
            f"ACTION CANCELLED: {plan.label} (confirmation expired)"
            if note == "EXPIRED"
            else f"ACTION CANCELLED: {plan.label}"
        )
        self._append(ChatRole.SYSTEM, text, now, ResponseSource.SYSTEM, detail=note)
        self._note(f"{plan.label} CANCELLED", False, note)

    def _fail(
        self,
        kind: AIErrorKind,
        detail: str,
        now: float,
        fallback: bool = False,
    ) -> None:
        """Report a provider failure honestly, and answer locally if possible."""
        self._in_flight = False
        self._error = kind
        self._error_detail = detail or kind.label
        self._append(ChatRole.SYSTEM, f"{kind.label}", now, ResponseSource.SYSTEM, detail=detail)
        logger.info("AI request failed: %s (%s)", kind.label, detail)
        if fallback:
            context = self._context()
            result = local_answer(self._last_user_text(), context)
            if result is not None:
                reply = parse_local(result.text)
                self._append(
                    ChatRole.ASSISTANT,
                    reply.text,
                    now,
                    reply.source,
                    detail="LOCAL FALLBACK",
                )
        self._set_status(AIStatus.ERROR, now)

    def _last_user_text(self) -> str:
        for message in reversed(self._messages):
            if message.role is ChatRole.USER:
                return message.content
        return ""


def _normalise_plan(plan: AIActionPlan) -> Tuple[Optional[AIActionPlan], str]:
    """Re-check a parsed plan against the allowlist before it can be executed."""
    spec, reason = normalise(plan)
    if spec is None:
        return None, reason
    return (
        AIActionPlan(
            kind=spec.kind,
            action=spec.action,
            label=spec.label,
            requires_confirmation=spec.requires_confirmation,
        ),
        "",
    )


def _outcome_text(outcome: AIActionOutcome) -> str:
    """The honest, user-visible result of one AI action."""
    plan = outcome.plan
    if outcome.success:
        detail = f" ({outcome.message})" if outcome.message and outcome.message != plan.label else ""
        return f"ACTION DONE: {plan.label}{detail}"
    if outcome.accepted and outcome.executed:
        return f"ACTION FAILED: {outcome.message or plan.label} ({outcome.reason or 'backend refused'})"
    return f"ACTION REFUSED: {plan.label} ({outcome.reason or 'not allowed right now'})"
