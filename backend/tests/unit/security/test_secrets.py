"""Secrets stay out of logs, traces, prompts, and error messages (GRD-8, PRD §11)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from pydantic import SecretStr

from repoatlas.answers.prompting import build_answer_messages
from repoatlas.answers.schema import AnswerDraft, number_evidence
from repoatlas.config import LogFormat, Settings
from repoatlas.llm.client import LLMClient
from repoatlas.llm.compat import build_request
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.transport import LiteLLMTransport, error_response, scrub
from repoatlas.llm.types import LLMRequestError
from repoatlas.logging import configure, configure_logging, get_logger
from repoatlas.redact import REDACTED, forget_secrets, redact, redact_value, register_secret
from repoatlas.tracing import start_trace, trace_span
from repoatlas.tracing.core import Trace
from tests.unit.answers.helpers import EVIDENCE
from tests.unit.api.helpers import api_settings

API_KEY = "sk-ant-api03-TESTONLY0000aaaaBBBBccccDDDD"
GITHUB_TOKEN = "ghp_TESTONLY0000aaaaBBBBccccDDDDeeee1234"
ACCESS_TOKEN = "correct-horse-battery-staple-TESTONLY"
OTLP_HEADERS = "Authorization=Basic dGVzdG9ubHk6c2Vrcml0dmFsdWU=,x-team=maps"
SECRETS = (API_KEY, GITHUB_TOKEN, ACCESS_TOKEN, "dGVzdG9ubHk6c2Vrcml0dmFsdWU=")


def settings() -> Settings:
    return api_settings(
        anthropic_api_key=API_KEY,
        github_token=GITHUB_TOKEN,
        access_token=ACCESS_TOKEN,
        otel_exporter_otlp_headers=OTLP_HEADERS,
    )


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    forget_secrets()
    yield
    forget_secrets()
    structlog.reset_defaults()


def clear_of_secrets(text: str) -> bool:
    return not any(secret in text for secret in SECRETS)


def test_settings_never_show_their_secrets() -> None:
    config = settings()
    for rendered in (repr(config), str(config), config.model_dump_json(), str(config.model_dump())):
        assert clear_of_secrets(rendered), rendered
    assert isinstance(config.anthropic_api_key, SecretStr)
    assert config.anthropic_api_key.get_secret_value() == API_KEY  # still usable on purpose


def test_redact_removes_registered_values_and_credential_shapes() -> None:
    assert redact("key sk-ant-api03-abcdefGHIJKL_123-xyz end") == f"key {REDACTED} end"
    assert redact("ghp_abcdefghijklmnopqrstuvwxyz0123456789") == REDACTED
    assert redact("github_pat_11ABCDEFG0abcdefghijklmnop_qrstuv") == REDACTED
    assert redact("Authorization: Bearer abc.def-ghi_123456") == f"Authorization: Bearer {REDACTED}"
    assert redact("Authorization: Basic dXNlcjpwYXNzd29yZA==") == f"Authorization: Basic {REDACTED}"
    # A value with no recognizable shape is removed once it is registered.
    assert redact(f"token={ACCESS_TOKEN}") == f"token={ACCESS_TOKEN}"
    register_secret(ACCESS_TOKEN)
    assert redact(f"token={ACCESS_TOKEN}&x=1") == f"token={REDACTED}&x=1"
    register_secret("short")  # too short to match safely: ignored
    assert redact("a short word") == "a short word"
    assert redact("nothing secret: sk-learn, ghp, Bearer of news") == (
        "nothing secret: sk-learn, ghp, Bearer of news"
    )
    nested = {"a": [f"x {API_KEY}", ("y", API_KEY)], "n": 5, "ok": None}
    assert redact_value(nested) == {"a": [f"x {REDACTED}", ("y", REDACTED)], "n": 5, "ok": None}


@pytest.mark.parametrize("fmt", [LogFormat.JSON, LogFormat.CONSOLE])
def test_no_secret_reaches_a_log_line(fmt: LogFormat, capsys: pytest.CaptureFixture[str]) -> None:
    config = settings().model_copy(update={"log_format": fmt, "log_level": "DEBUG"})
    configure(config)  # registers the settings' secrets
    log = get_logger("test")
    log.info("request failed", url=f"https://api.example/v1?key={API_KEY}", token=ACCESS_TOKEN)
    log.warning(f"header was Authorization: Bearer {GITHUB_TOKEN}")
    log.info("nested", payload={"headers": [OTLP_HEADERS], "attempt": 2})
    try:
        raise RuntimeError(f"upstream said: invalid x-api-key {API_KEY}")
    except RuntimeError:
        log.exception("unhandled error")
    logging.getLogger("thirdparty").warning("connecting with %s", GITHUB_TOKEN)
    written = capsys.readouterr().err
    assert clear_of_secrets(written), written
    assert written.count(REDACTED) >= 6
    assert "request failed" in written and "RuntimeError" in written and "attempt" in written


def test_a_secret_in_an_exception_is_not_stored_on_a_trace_span() -> None:
    class Sink:
        trace: Trace | None = None

        def write(self, trace: Trace) -> None:
            self.trace = trace

    sink = Sink()
    register_secret(ACCESS_TOKEN)
    with (
        pytest.raises(RuntimeError),
        start_trace("answer", sink=sink),
        trace_span("llm.call", "llm"),
    ):
        raise RuntimeError(f"401 for key {API_KEY} and token {ACCESS_TOKEN}")
    assert sink.trace is not None
    (span,) = sink.trace.spans
    assert span.status == "error"
    assert clear_of_secrets(json.dumps(span.attrs))
    assert span.attrs["error"] == f"RuntimeError: 401 for key {REDACTED} and token {REDACTED}"


def test_llm_error_messages_are_scrubbed() -> None:
    error = error_response(401, f"AuthenticationError: invalid x-api-key: {API_KEY}")
    assert isinstance(error, LLMRequestError) and clear_of_secrets(str(error))
    assert REDACTED in str(error)
    busy = error_response(529, f"Overloaded (key {API_KEY})", retry_after_s=2.0)
    assert clear_of_secrets(str(busy))
    assert scrub("no key here") == "no key here"


@pytest.mark.anyio
async def test_the_api_key_is_never_part_of_a_request_body_or_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    class FakeMessages:
        @staticmethod
        async def acreate(**kwargs: Any) -> dict[str, Any]:
            sent.update(kwargs)
            draft = {"answer_markdown": "ok", "claims": [], "refused": True, "refusal_reason": "x"}
            return {
                "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": json.dumps(draft)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

    class FakeLiteLLM:
        Timeout = TimeoutError
        APIConnectionError = ConnectionError

        class anthropic:
            messages = FakeMessages

    monkeypatch.setattr("repoatlas.llm.transport._litellm", lambda: FakeLiteLLM)
    client = LLMClient(
        LiteLLMTransport(SecretStr(API_KEY)),
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        efforts={"small": "low", "large": "medium"},
        prices=PriceTable(DEFAULT_PRICES),
        max_retries=0,
    )
    messages = build_answer_messages("How is the app created?", number_evidence(EVIDENCE))
    result = await client.complete("large", messages, schema=AnswerDraft, max_tokens=500)
    assert isinstance(result.parsed, AnswerDraft)
    # The key travels only as LiteLLM's api_key argument, never inside the request body.
    assert sent.pop("api_key") == API_KEY
    assert clear_of_secrets(json.dumps(sent, default=str))
    assert clear_of_secrets(json.dumps(messages))
    assert clear_of_secrets(json.dumps(build_request("m", messages, max_tokens=9)))
    assert clear_of_secrets(repr(result)) and clear_of_secrets(repr(client.__dict__))


def test_logging_can_be_configured_without_settings() -> None:
    configure_logging("INFO", LogFormat.JSON)  # no secrets registered: shapes still apply
    assert redact(API_KEY) == REDACTED
