"""Keep secrets out of logs, traces, and error messages (GRD-8, PRD §11).

``redact(text)`` replaces anything that is a secret or looks like one with ``<redacted>``:

- the values of this process's own secrets (API key, GitHub token, access token, …), which
  ``register_secrets(settings)`` records when the process configures itself;
- anything shaped like an Anthropic API key or a GitHub token, whatever its source;
- the value after ``Bearer`` or ``Basic`` in an ``Authorization`` header.

It is applied where free text leaves the code's hands: every log event and traceback
(``repoatlas.logging``), the error text stored on a trace span, and LLM error messages. It is a
last line of defense: the first is that secrets are ``SecretStr`` settings that are never put
in prompts, request bodies, span attributes, or log fields to begin with.
"""

from __future__ import annotations

import re
import threading
from typing import Any

REDACTED = "<redacted>"
MIN_SECRET_CHARS = 8  # shorter configured values are not worth matching (and would over-match)

_SHAPES = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]{6,}"  # Anthropic API keys
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"  # GitHub tokens
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|(?<=\bBearer )[A-Za-z0-9._~+/\-]{8,}=*"  # Authorization values
    r"|(?<=\bBasic )[A-Za-z0-9+/]{8,}=*"
)
_lock = threading.Lock()
_values: set[str] = set()
_pattern: re.Pattern[str] | None = None


def register_secret(value: str | None) -> None:
    """Remember ``value`` so ``redact`` removes it wherever it appears."""
    global _pattern
    if not value or len(value) < MIN_SECRET_CHARS:
        return
    with _lock:
        if value in _values:
            return
        _values.add(value)
        ordered = sorted(_values, key=len, reverse=True)  # longest first: no partial matches
        _pattern = re.compile("|".join(re.escape(item) for item in ordered))


def register_secrets(settings: Any) -> None:
    """Register every secret setting of ``settings`` (its ``SecretStr`` fields)."""
    from pydantic import SecretStr

    for name in type(settings).model_fields:
        value = getattr(settings, name, None)
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
            register_secret(secret)
            # A header list holds several values: "Authorization=Basic abc,x-key=def".
            for part in re.split(r"[,=\s]+", secret):
                register_secret(part)


def forget_secrets() -> None:
    """Drop the registered values (tests)."""
    global _pattern
    with _lock:
        _values.clear()
        _pattern = None


def redact(text: str) -> str:
    """``text`` without registered secrets or anything shaped like a credential."""
    pattern = _pattern
    if pattern is not None:
        text = pattern.sub(REDACTED, text)
    return _SHAPES.sub(REDACTED, text)


def redact_value(value: Any) -> Any:
    """``redact`` through strings, lists, tuples, and dicts; other values unchanged."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value
