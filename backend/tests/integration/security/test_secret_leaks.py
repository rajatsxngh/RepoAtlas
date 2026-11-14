"""No secret leaves through the API (GRD-8): not in an error, a stored trace, or a log line,
even when a failure's own message contains one."""

from __future__ import annotations

import json

import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy import text

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.config import LogFormat
from repoatlas.llm.fake import FakeLLM
from repoatlas.logging import configure_logging
from repoatlas.redact import REDACTED, forget_secrets
from tests.integration.api.test_ask import ask, events, install
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.api.helpers import api_settings, client_for

pytestmark = pytest.mark.anyio

API_KEY = "sk-ant-api03-TESTONLY1111eeeeFFFFggggHHHH"
ACCESS_TOKEN = "an-access-token-for-tests-TESTONLY-42"


async def test_a_failure_that_quotes_a_key_leaks_it_nowhere(
    pg_url: str, tmp_path: object, clean_store: None, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = api_settings(
        database_url=pg_url,
        data_dir=tmp_path,
        anthropic_api_key=API_KEY,
        access_token=ACCESS_TOKEN,
    )
    # Log to this test's stderr, whatever an earlier test configured.
    configure_logging("INFO", LogFormat.JSON)
    app: FastAPI = create_app(settings)
    try:
        snapshot_id = store_fixture(app.state.engine, "py_small")
        boom = RuntimeError(f"401 Unauthorized: x-api-key {API_KEY} (token {ACCESS_TOKEN})")
        install(app, FakeLLM([boom]))
        async with client_for(app) as client:
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
            response = await client.post(
                f"{API_PREFIX}/snapshots/{snapshot_id}/ask",
                json={"question": "How does the cart compute its total?"},
                headers=headers,
            )
            assert response.status_code == 200
            stream = events(response.text)
            assert stream[-1][0] == "error"
            # The client gets a generic message, never the exception's text.
            assert stream[-1][1]["error"]["message"] == "Answering failed unexpectedly."
            unauthorized = await client.get(f"{API_PREFIX}/snapshots/{snapshot_id}")
            assert unauthorized.status_code == 401
        with app.state.engine.connect() as connection:
            stored = (
                connection.execute(text("SELECT attrs FROM spans WHERE status = 'error'"))
                .scalars()
                .all()
            )
        logged = capsys.readouterr().err
    finally:
        app.state.engine.dispose()
        forget_secrets()
        structlog.reset_defaults()
    assert stored, "the failed answer's trace was stored"
    everything = response.text + unauthorized.text + json.dumps(stored) + logged
    assert API_KEY not in everything and ACCESS_TOKEN not in everything
    assert REDACTED in json.dumps(stored)  # the span's error text was kept, without the key
    assert "ask_failed" in logged and REDACTED in logged  # the failure is still logged


async def test_answers_and_traces_never_contain_settings_secrets(app: FastAPI) -> None:
    from tests.integration.api.test_ask import drafting

    snapshot_id = store_fixture(app.state.engine, "py_small")
    llm = FakeLLM(lambda call: drafting(call))
    install(app, llm)
    response = await ask(app, snapshot_id)
    done = events(response.text)[-1][1]
    async with client_for(app) as client:
        trace = await client.get(f"{API_PREFIX}/traces/{done['trace_id']}")
        health = await client.get(f"{API_PREFIX}/health")
        ready = await client.get(f"{API_PREFIX}/ready")
        spec = await client.get(f"{API_PREFIX}/openapi.json")
    database_password = app.state.settings.database_url.split(":")[2].split("@")[0]
    prompt = json.dumps(llm.calls[0].messages)
    for body in (response.text, trace.text, health.text, ready.text, prompt):
        assert database_password not in body or len(database_password) < 4
        assert "postgresql+psycopg://" not in body
        assert "sk-ant-" not in body and "Bearer " not in body
    assert "REPOATLAS_" not in spec.text  # the spec names no environment variables or values
