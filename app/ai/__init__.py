"""VisionCore AI - the Phase 7 assistant layer.

The package is deliberately small and closed:

* :mod:`app.ai.types` - typed conversation, status, plan and result vocabulary;
* :mod:`app.ai.settings` - configuration read from the environment only;
* :mod:`app.ai.context` - the sanitized state block the assistant may see;
* :mod:`app.ai.provider` - the replaceable provider interface plus the
  OpenAI-compatible implementation, on the standard library alone;
* :mod:`app.ai.client` - one background worker so a request never blocks a frame;
* :mod:`app.ai.parser` - strict reading of an answer into a typed reply;
* :mod:`app.ai.router` - the closed action allowlist and the route into the
  existing intent router and safety gates;
* :mod:`app.ai.local` - deterministic local answers when no provider is present;
* :mod:`app.ai.assistant` - the state machine that ties it together.

There is no shell, no subprocess, no ``eval`` and no operating-system call
anywhere in this package: an answer can only become one of the actions in
:data:`app.ai.router.ACTION_TABLE`, and that action still has to pass the
control layers that existed before this phase.
"""

from __future__ import annotations

from app.ai.assistant import AIAssistant
from app.ai.context import AIContext, build_context
from app.ai.provider import AIProvider, NullProvider, OpenAICompatibleProvider
from app.ai.router import ACTION_TABLE, ActionRouter
from app.ai.settings import load_settings
from app.ai.types import (
    AIActionKind,
    AIActionOutcome,
    AIActionPlan,
    AIErrorKind,
    AIReply,
    AIRequest,
    AIResult,
    AISettings,
    AISnapshot,
    AIStatus,
    ChatMessage,
    ChatRole,
    ResponseSource,
)

__all__ = [
    "ACTION_TABLE",
    "AIActionKind",
    "AIActionOutcome",
    "AIActionPlan",
    "AIAssistant",
    "AIContext",
    "AIErrorKind",
    "AIProvider",
    "AIReply",
    "AIRequest",
    "AIResult",
    "AISettings",
    "AISnapshot",
    "AIStatus",
    "ActionRouter",
    "ChatMessage",
    "ChatRole",
    "NullProvider",
    "OpenAICompatibleProvider",
    "ResponseSource",
    "build_context",
    "load_settings",
]
