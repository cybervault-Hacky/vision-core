"""Strict parsing of provider answers into typed replies.

A provider answer is only ever accepted as the small JSON object the system
prompt asks for::

    {"reply": "text", "action": {"type": "DEVICE_ACTION", "action": "VOLUME_UP"}}
    {"reply": "text", "action": {"type": "CONTROL_MODE", "mode": "DEVICE"}}
    {"reply": "text"}

Anything that cannot be read as that shape is refused as
``AI RESPONSE UNREADABLE`` and nothing is executed, because the answer is never
interpreted as code: it is read as data, and data that does not match the schema
simply does not become a reply. An action name outside the allowlist is reported
as ``ACTION REFUSED``; a request for a shutdown, a restart, a logout, a shell
command or any other system change gets the fixed sentence from
:data:`app.ai.router.UNAVAILABLE_SENTENCE` and is never constructed at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.ai.router import UNAVAILABLE_SENTENCE, is_blocked, resolve_kind
from app.ai.types import (
    AIActionKind,
    AIActionPlan,
    AIErrorKind,
    AIReply,
    ResponseSource,
)

# A reply longer than this is truncated rather than trusted.
MAX_TEXT_CHARS = 2000
# Keys accepted for the answer text (the prompt asks for "reply").
_TEXT_KEYS = ("reply", "text", "answer", "message")
# Key accepted for the action name, per family.
_ACTION_KEYS: Dict[AIActionKind, Tuple[str, ...]] = {
    AIActionKind.DEVICE_ACTION: ("action", "name"),
    AIActionKind.CONTROL_MODE: ("mode", "action", "name"),
    AIActionKind.CONTROL_ACTION: ("action", "name"),
}
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Outcome of reading one provider answer.

    ``reply`` is the usable part (text, and optionally a plan that still has to
    pass the allowlist and the safety gates). ``error`` is set when something in
    the answer was refused, and ``reply`` is ``None`` when nothing usable came
    back at all.
    """

    reply: Optional[AIReply] = None
    error: Optional[AIErrorKind] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reply is not None and self.error is None


def _extract_object(text: str) -> Optional[Dict[str, Any]]:
    """Find exactly one JSON object in the answer, or nothing."""
    candidate = (text or "").strip()
    if not candidate:
        return None

    attempts = [candidate]
    fenced = _FENCE.findall(candidate)
    attempts.extend(block.strip() for block in fenced)
    first, last = candidate.find("{"), candidate.rfind("}")
    if 0 <= first < last:
        attempts.append(candidate[first : last + 1])

    for attempt in attempts:
        if not attempt.startswith("{"):
            continue
        try:
            decoded = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _text_of(decoded: Dict[str, Any]) -> Optional[str]:
    for key in _TEXT_KEYS:
        value = decoded.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_TEXT_CHARS]
    return None


def _plan_for(raw: object) -> Tuple[Optional[AIActionPlan], Optional[AIErrorKind], str]:
    """Turn the action block into a plan, or explain why it cannot be one."""
    if raw is None:
        return None, None, ""
    if not isinstance(raw, dict):
        return None, AIErrorKind.REJECTED, "Action was not a structured object"

    kind = resolve_kind(raw.get("type"))
    if kind is None:
        return None, AIErrorKind.REJECTED, "Unknown action type"

    name = ""
    for key in _ACTION_KEYS[kind]:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break
    if not name:
        return None, AIErrorKind.REJECTED, "Action had no name"
    if is_blocked(name):
        return None, AIErrorKind.REJECTED, "BLOCKED REQUEST"

    normalised = name.upper().replace(" ", "_").replace("-", "_")[:40]
    return (
        AIActionPlan(
            kind=kind,
            action=normalised,
            label=normalised.replace("_", " "),
        ),
        None,
        "",
    )


def parse_reply(text: str) -> ParseResult:
    """Read one provider answer into a typed reply."""
    decoded = _extract_object(text)
    if decoded is None:
        return ParseResult(
            reply=None,
            error=AIErrorKind.MALFORMED,
            detail="Response was not a JSON object",
        )

    body = _text_of(decoded)
    plan, error, detail = _plan_for(decoded.get("action"))

    if body is None:
        # An action without an answer is still readable, but an empty answer is
        # not a conversation turn: report it instead of inventing words.
        if plan is not None:
            body = plan.label
        else:
            return ParseResult(
                reply=None,
                error=AIErrorKind.MALFORMED,
                detail="Response carried no answer text",
            )

    if detail == "BLOCKED REQUEST":
        # Never even keep such a request in the conversation.
        return ParseResult(
            reply=AIReply(text=UNAVAILABLE_SENTENCE, source=ResponseSource.PROVIDER),
            error=AIErrorKind.REJECTED,
            detail="BLOCKED REQUEST",
        )

    return ParseResult(
        reply=AIReply(text=body, action=plan, source=ResponseSource.PROVIDER),
        error=error,
        detail=detail,
    )


def parse_local(text: str) -> AIReply:
    """Wrap a deterministic local answer with its own, honest source tag."""
    return AIReply(
        text=text.strip()[:MAX_TEXT_CHARS],
        action=None,
        source=ResponseSource.LOCAL,
    )
