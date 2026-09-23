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
from dataclasses import replace
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
from app.interaction.multimodal import InputEnvelope, InputSource
from app.ai.commands import match_command

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

# Why a refusal happened, in the user's terms. A gate that says "no" is only
# useful if the interface also says what would make it a "yes" - and none of
# these ever enables anything by itself.
_REFUSAL_HINTS = {
    "DEVICE CONTROL NOT ENABLED": (
        "Device control is currently disabled, so {label} was not performed. "
        "Enable device control before asking me again."
    ),
    "DEVICE DISABLED": (
        "Device control is currently disabled, so {label} was not performed. "
        "Enable device control before asking me again."
    ),
    "MOUSE CONTROL NOT ENABLED": (
        "Mouse control is currently disabled, so {label} was not performed. "
        "Enable control before asking me again."
    ),
    "CONTROL NOT ENABLED": (
        "Control is currently disabled, so {label} was not performed. Enable "
        "control before asking me again."
    ),
    "EMERGENCY STOP ACTIVE": (
        "An emergency stop is active, so {label} was not performed. Only the "
        "deliberate recovery action clears it."
    ),
}

# Used when a platform genuinely does not offer a capability.
UNAVAILABLE_HINT = (
    "The operating system does not expose that capability here, so {label} is "
    "reported as unavailable instead of pretending to work."
)


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
        self._last_input: Optional[InputEnvelope] = None
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

    def send(self, text: str, source: InputSource = InputSource.TEXT) -> bool:
        """Send one message. Never blocks and never calls the network directly.

        ``source`` records how the message arrived (typed or spoken) so the
        conversation can label it. Both travel the identical pipeline: the same
        parser, the same allowlist and the same safety gates.
        """
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
        envelope = InputEnvelope.query(source, message, now)

        self._last_input = envelope
        self._cancel_pending(note="SUPERSEDED")
        self._append(
            ChatRole.USER, message, now, ResponseSource.SYSTEM, input=envelope
        )
        self.turns += 1
        logger.info("Assistant input (%s)", envelope.summary())

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

    def send_transcript(self, text: str) -> bool:
        """Deliver a voice transcript, and never drop one silently.

        A transcript is a user message like any other; the only difference is the
        provenance tag it carries. If the assistant is still busy the message is
        reported as not sent instead of disappearing.
        """
        if self.send(text, source=InputSource.VOICE):
            return True
        self._append(
            ChatRole.SYSTEM,
            "VOICE INPUT NOT SENT: the previous request is still in progress",
            self._clock(),
            ResponseSource.SYSTEM,
            detail="DROPPED",
        )
        self._note("VOICE INPUT DROPPED", False, "ASSISTANT BUSY")
        return False

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

    def emergency_cancel(self) -> bool:
        """Drop a waiting action the instant an emergency stop engages.

        An emergency stop outranks everything the assistant can ask for, so a
        proposal that has not run yet is withdrawn rather than left sitting on
        screen waiting for a confirmation that must not be honoured.
        """
        plan = self._pending
        if plan is None:
            return False
        self._pending = None
        now = self._clock()
        self._append(
            ChatRole.SYSTEM,
            f"ACTION CANCELLED: {plan.label} (emergency stop)",
            now,
            ResponseSource.SYSTEM,
            detail="EMERGENCY",
        )
        self._note(f"{plan.label} CANCELLED", False, "EMERGENCY STOP")
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
        """Fresh state snapshot: built when a message is sent, never per frame."""
        context = self._context_provider()
        envelope = self._last_input
        if envelope is None or context.input_source == envelope.source.label:
            return context
        # A transcript can be imperfect; telling the provider where the words came
        # from is honest data, not extra telemetry.
        return replace(context, input_source=envelope.source.label)

    def _append(
        self,
        role: ChatRole,
        content: str,
        now: float,
        source: ResponseSource,
        detail: str = "",
        input: Optional[InputEnvelope] = None,
    ) -> ChatMessage:
        """Add one conversation entry, keeping its input provenance."""
        message = ChatMessage(
            role=role,
            content=content,
            timestamp=now,
            source=source,
            detail=detail,
            **({"input": input} if input is not None else {}),
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
        """Deterministic path: used when no provider can answer.

        A literal command from the closed vocabulary is executed through the same
        gates an action from a model would use; a state question is answered from
        telemetry; anything else gets the honest "no provider" note.
        """
        plan = match_command(message)
        if plan is not None:
            self._local_command(plan, now)
            return
        context = self._context()
        result = local_answer(message, context)
        text = result.text if result is not None else off_topic_answer()
        reply = parse_local(text)
        self._append(
            ChatRole.ASSISTANT, reply.text, now, reply.source, detail="LOCAL"
        )
        self._hold(AIStatus.RESPONDING, now)

    def _local_command(self, plan: AIActionPlan, now: float) -> None:
        """Run a locally recognised command - never claiming it came from a model."""
        logger.info("Local command recognised: %s", plan.signature)
        self._error = None
        self._error_detail = ""
        self._append(
            ChatRole.ASSISTANT,
            f"Understood: {plan.label}.",
            now,
            ResponseSource.LOCAL,
            detail="LOCAL COMMAND",
        )
        self._apply_plan(plan, now)

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
        self._apply_plan(reply.action, now)

    def _apply_plan(self, plan: AIActionPlan, now: float) -> bool:
        """Allowlist, then confirmation, then execution.

        Every plan - from a provider, from a local command phrase or from a
        confirmation click - passes through here, so the two-step confirmation
        rule cannot be bypassed by choosing a different input method.
        """
        spec, reason = _normalise_plan(plan)
        if spec is None:
            self._append(
                ChatRole.SYSTEM,
                f"ACTION REFUSED: {reason}",
                now,
                ResponseSource.SYSTEM,
                detail="REFUSED",
            )
            self._note(f"{plan.label} REFUSED", False, reason)
            return False

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
            return True

        self._execute(spec, now)
        return True

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
        hint = _refusal_hint(outcome)
        if hint:
            # The gate said no; say what would make it a yes. Nothing is enabled
            # here - the user still has to do it deliberately.
            self._append(
                ChatRole.SYSTEM, hint, now, ResponseSource.SYSTEM, detail="REFUSED"
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
            # A literal command never needed a language model, so a provider that
            # is down must not stop the words VisionCore already understands: the
            # failure is reported above, and the command travels the ordinary
            # allowlist and safety gates exactly as it would with a provider.
            plan = match_command(self._last_user_text())
            if plan is not None:
                self._error = None
                self._error_detail = ""
                self._set_status(AIStatus.READY, now)
                self._local_command(plan, now)
                return
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


def _refusal_hint(outcome: AIActionOutcome) -> str:
    """Explain a refusal in the user's terms, or return an empty string.

    The hint names the action that did *not* run, and it only ever explains why:
    nothing is enabled and nothing is retried by explaining a refusal.
    """
    if outcome.success:
        return ""
    label = outcome.plan.label
    hint = _REFUSAL_HINTS.get(outcome.reason or "")
    if hint:
        return hint.format(label=label)
    if (outcome.reason or "").endswith("UNAVAILABLE ON THIS SYSTEM"):
        return UNAVAILABLE_HINT.format(label=label)
    return ""


def _outcome_text(outcome: AIActionOutcome) -> str:
    """The honest, user-visible result of one AI action."""
    plan = outcome.plan
    if outcome.success:
        detail = f" ({outcome.message})" if outcome.message and outcome.message != plan.label else ""
        return f"ACTION DONE: {plan.label}{detail}"
    if outcome.accepted and outcome.executed:
        return f"ACTION FAILED: {outcome.message or plan.label} ({outcome.reason or 'backend refused'})"
    return f"ACTION REFUSED: {plan.label} ({outcome.reason or 'not allowed right now'})"
