"""Transports send one Messages API request body and return the response body.

``LiteLLMTransport`` sends through LiteLLM's Anthropic-format interface
(``litellm.anthropic.messages.acreate``), which forwards the body as written, so the request
rules in ``repoatlas.llm.compat`` reach the API unchanged. LiteLLM's own retries are off; the
client retries. Errors become ``TransientLLMError`` (retry) or ``LLMRequestError`` (don't).

Transports never see or return HTTP headers, so recorded responses can't contain secrets, and
error messages are scrubbed of anything that looks like an API key.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

from pydantic import SecretStr

from repoatlas.llm.types import LLMRequestError, TransientLLMError

_KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
TRANSIENT_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class Transport(Protocol):
    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]: ...


def scrub(message: str) -> str:
    """``message`` without anything shaped like an Anthropic API key."""
    return _KEY.sub("<redacted>", message)


def error_response(status: int, message: str) -> TransientLLMError | LLMRequestError:
    """The error a response with HTTP ``status`` raises."""
    if status in TRANSIENT_STATUSES:
        return TransientLLMError(scrub(message), status=status)
    return LLMRequestError(scrub(message), status=status)


class LiteLLMTransport:
    """Calls the Anthropic API through LiteLLM with ``api_key``."""

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        litellm = _litellm()
        body = dict(request)
        model = body.pop("model")
        try:
            response = await litellm.anthropic.messages.acreate(
                model=f"anthropic/{model}",
                api_key=self._api_key.get_secret_value(),
                timeout=timeout_s,
                num_retries=0,
                **body,
            )
        except (litellm.Timeout, litellm.APIConnectionError) as exc:
            raise TransientLLMError(scrub(f"{type(exc).__name__}: {exc}")) from None
        except Exception as exc:
            status = getattr(exc, "status_code", None)  # litellm's errors carry the HTTP status
            if not isinstance(status, int):
                raise
            raise error_response(status, f"{type(exc).__name__}: {exc}") from None
        if not isinstance(response, dict):
            raise LLMRequestError("LiteLLM returned a stream for a non-streaming request.")
        return dict(response)


def _litellm() -> Any:
    """Import LiteLLM on first use (it is slow to import) with its network features off."""
    # Use the bundled model map instead of fetching one at import; prices come from settings.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.suppress_debug_info = True
    litellm.telemetry = False
    return litellm
