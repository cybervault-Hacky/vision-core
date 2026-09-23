"""AI provider abstraction and the OpenAI-compatible implementation.

The application only ever talks to :class:`AIProvider`. Swapping in another
backend means implementing ``chat(messages, context) -> ProviderReply``; nothing
else in VisionCore changes.

Transport notes:

* the call is made with the standard library (``urllib``), so no new dependency
  is added and no SDK can quietly add analytics or telemetry;
* only ``http``/``https`` endpoints are accepted, redirects are refused, the
  response body is read with a hard size cap, and the request carries exactly
  the conversation plus the constructed state block - never a camera frame, an
  image, audio or a file;
* every failure is mapped onto :class:`AIErrorKind` so the interface can report
  what actually went wrong instead of a generic error.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from app.ai.prompt import build_messages
from app.ai.types import (
    AIErrorKind,
    AISettings,
    ChatMessage,
    ChatRole,
)

logger = logging.getLogger("visioncore.ai.provider")

# Hard cap on the response body we are willing to read (1 MiB).
MAX_RESPONSE_BYTES = 1024 * 1024
# Cap on the answer length accepted from a provider (characters).
MAX_REPLY_CHARS = 4000


class ProviderError(Exception):
    """A provider failure that the assistant can report honestly."""

    def __init__(self, kind: AIErrorKind, detail: str = "") -> None:
        super().__init__(detail or kind.label)
        self.kind = kind
        self.detail = detail or kind.label


@dataclass(frozen=True, slots=True)
class ProviderReply:
    """Raw text returned by a provider, before parsing."""

    text: str
    truncated: bool = False


class AIProvider(ABC):
    """Replaceable conversational backend."""

    name = "NONE"
    model = ""

    @property
    def available(self) -> bool:
        """True when the provider can actually be called."""
        return False

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        context: str,
    ) -> ProviderReply:
        """Return a reply for the conversation so far, or raise ProviderError."""


class NullProvider(AIProvider):
    """Used when no provider is configured: it never pretends to be one."""

    name = "NONE"

    def __init__(self, detail: str = "No AI provider is configured") -> None:
        self._detail = detail

    @property
    def available(self) -> bool:
        return False

    def chat(self, messages: Sequence[ChatMessage], context: str) -> ProviderReply:
        raise ProviderError(AIErrorKind.NOT_CONFIGURED, self._detail)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so a configured endpoint cannot bounce elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        logger.warning("AI provider redirect refused (%s)", code)
        return None


class OpenAICompatibleProvider(AIProvider):
    """Chat-completions provider (OpenAI and compatible endpoints)."""

    name = "OPENAI_COMPATIBLE"

    def __init__(self, settings: AISettings) -> None:
        self.settings = settings
        self.model = settings.model
        self._opener = urllib.request.build_opener(_NoRedirect)

    @property
    def available(self) -> bool:
        return self.settings.configured

    # -- request ----------------------------------------------------------- #

    def chat(self, messages: Sequence[ChatMessage], context: str) -> ProviderReply:
        """Send the conversation plus the constructed state block."""
        if not self.settings.configured:
            raise ProviderError(AIErrorKind.NOT_CONFIGURED)

        payload = {
            "messages": build_messages(self._history(messages), context),
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if self.settings.model:
            payload["model"] = self.settings.model

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.api_key}",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=self.settings.timeout_sec) as response:
                raw = response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError(AIErrorKind.TIMEOUT, f"No answer within {self.settings.timeout_sec:.0f}s") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderError(AIErrorKind.TIMEOUT, "Connection timed out") from exc
            raise ProviderError(AIErrorKind.UNAVAILABLE, str(reason)) from exc
        except Exception as exc:  # never let transport take the application down
            raise ProviderError(AIErrorKind.UNAVAILABLE, type(exc).__name__) from exc

        return ProviderReply(text=self._extract_text(raw))

    # -- parsing ----------------------------------------------------------- #

    @staticmethod
    def _history(messages: Sequence[ChatMessage]) -> Tuple[dict, ...]:
        """Conversation entries only; system entries are not replayed as chat."""
        history = []
        for message in messages:
            if message.role is ChatRole.SYSTEM:
                continue
            role = "user" if message.role is ChatRole.USER else "assistant"
            history.append({"role": role, "content": message.content})
        return tuple(history)

    @staticmethod
    def _extract_text(raw: bytes) -> str:
        """Pull the assistant text out of the response, or report malformed."""
        try:
            decoded = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ProviderError(AIErrorKind.MALFORMED, "Response was not JSON") from exc

        if not isinstance(decoded, dict):
            raise ProviderError(AIErrorKind.MALFORMED, "Response was not an object")

        error = decoded.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", ""))[:200]
            raise ProviderError(AIErrorKind.UNAVAILABLE, message)

        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(AIErrorKind.MALFORMED, "Response carried no choices")

        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderError(AIErrorKind.MALFORMED, "Malformed choice entry")

        message = first.get("message")
        text: Optional[str] = None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
        if text is None:
            # Some compatible servers answer with a plain "text" field.
            fallback = first.get("text")
            text = fallback if isinstance(fallback, str) else None
        if text is None:
            raise ProviderError(AIErrorKind.MALFORMED, "Response carried no text")

        text = text.strip()
        if not text:
            raise ProviderError(AIErrorKind.MALFORMED, "Provider returned an empty answer")
        return text[:MAX_REPLY_CHARS]

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> ProviderError:
        """Map an HTTP status onto an honest, non-leaking error."""
        code = int(getattr(exc, "code", 0) or 0)
        if code in (401, 403):
            return ProviderError(AIErrorKind.AUTH, f"HTTP {code}")
        if code == 429:
            return ProviderError(AIErrorKind.RATE_LIMIT, "HTTP 429")
        if code == 404:
            return ProviderError(AIErrorKind.UNAVAILABLE, "HTTP 404 endpoint not found")
        if 500 <= code < 600:
            return ProviderError(AIErrorKind.UNAVAILABLE, f"HTTP {code}")
        return ProviderError(AIErrorKind.UNAVAILABLE, f"HTTP {code}")


def create_provider(settings: AISettings) -> AIProvider:
    """Build the provider described by the settings (never raises)."""
    if not settings.configured:
        reason = (
            "No AI provider is configured"
            if settings.provider in ("", "none")
            else "No API key provided"
        )
        return NullProvider(reason)
    if settings.provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    return NullProvider("Unsupported provider")


def provider_note(settings: AISettings, provider: AIProvider) -> str:
    """Short, secret-free description for logs and diagnostics."""
    return (
        f"{provider.name} (model {settings.model or 'default'}, "
        f"key {settings.masked_key()})"
    )
