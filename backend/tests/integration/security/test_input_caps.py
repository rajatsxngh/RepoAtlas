"""Oversized inputs are refused before they cost anything (GRD-8): questions, diffs, symbol keys,
and whole request bodies are capped, and a refused request never reaches the model."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI

from repoatlas.api.app import API_PREFIX
from repoatlas.api.routes.qa import MAX_QUESTION_CHARS
from repoatlas.api.schemas import MAX_KEY_CHARS, MAX_PATH_CHARS
from repoatlas.impact.schema import MAX_DIFF_CHARS
from repoatlas.llm.fake import FakeLLM
from tests.integration.api.test_ask import drafting, install
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.api.helpers import client_for, error_of

pytestmark = pytest.mark.anyio


async def post(app: FastAPI, path: str, body: Any, *, raw: bytes | None = None) -> Any:
    async with client_for(app) as client:
        if raw is not None:
            return await client.post(
                f"{API_PREFIX}{path}", content=raw, headers={"Content-Type": "application/json"}
            )
        return await client.post(f"{API_PREFIX}{path}", json=body)


async def test_questions_are_capped_and_a_refused_one_never_reaches_the_model(
    app: FastAPI,
) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    llm = FakeLLM(lambda call: drafting(call))
    install(app, llm)
    ask = f"/snapshots/{snapshot_id}/ask"
    for question in ("x" * (MAX_QUESTION_CHARS + 1), "", "   ", "a\x00b" * 900):
        response = await post(app, ask, {"question": question})
        assert response.status_code == 422, question[:20]
        assert error_of(response)["code"] == "invalid_request"
    odd: list[Any] = [{"question": ["a list"]}, {"question": {"nested": 1}}, {"q": "x"}, []]
    for body in odd:
        assert (await post(app, ask, body)).status_code == 422
    extra = await post(app, ask, {"question": "How does the cart compute its total?", "x": 1})
    assert extra.status_code == 422
    assert llm.calls == []  # nothing above was sent to the model
    # A question exactly at the cap is accepted.
    longest = "How does the cart compute its total? " + "x" * (MAX_QUESTION_CHARS - 37)
    accepted = await post(app, ask, {"question": longest})
    assert accepted.status_code == 200 and len(llm.calls) <= 1


async def test_request_bodies_over_the_limit_are_refused_whatever_they_hold(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    llm = FakeLLM([])
    install(app, llm)
    limit = app.state.settings.max_body_bytes
    huge = json.dumps({"question": "x" * (limit + 10)}).encode()
    padded = b'{"question": "ok"' + b" " * (limit + 10) + b"}"
    nested = (b"[" * 40_000) + (b"]" * 40_000)
    for path in (
        f"/snapshots/{snapshot_id}/ask",
        f"/snapshots/{snapshot_id}/impact",
        f"/snapshots/{snapshot_id}/impact/1/explain",
        "/repos",
    ):
        for raw in (huge, padded, nested):
            response = await post(app, path, None, raw=raw)
            assert response.status_code == 413, (path, response.status_code)
            assert error_of(response)["code"] == "invalid_request"
    assert llm.calls == []


async def test_diffs_and_symbol_keys_are_capped(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    impact = f"/snapshots/{snapshot_id}/impact"
    too_long = await post(app, impact, {"diff": "+" * (MAX_DIFF_CHARS + 1)})
    assert too_long.status_code in (413, 422)
    assert error_of(too_long)["code"] == "invalid_request"
    key = await post(app, impact, {"symbol_key": "py:" + "a" * (MAX_KEY_CHARS + 1)})
    assert key.status_code == 422
    params = await post(
        app, impact, {"symbol_key": "py:src/shop/cart.py:Cart:class", "params": {"limit": 10**9}}
    )
    assert params.status_code == 422
    depth = await post(
        app, impact, {"symbol_key": "py:src/shop/cart.py:Cart:class", "params": {"max_depth": 99}}
    )
    assert depth.status_code == 422


async def test_query_parameters_are_capped(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    base = f"{API_PREFIX}/snapshots/{snapshot_id}"
    async with client_for(app) as client:
        long_path = await client.get(f"{base}/code?path={'a' * (MAX_PATH_CHARS + 1)}&start=1&end=2")
        long_query = await client.get(f"{base}/search?q={'q' * 5_000}")
        big_limit = await client.get(f"{base}/search?q=cart&limit=100000")
        big_line = await client.get(f"{base}/code?path=src/shop/cart.py&start=1&end={2**40}")
        # However large the requested range, one read returns at most max_read_lines lines.
        capped = await client.get(f"{base}/code?path=src/shop/cart.py&start=1&end=2000000")
    for response in (long_path, long_query, big_limit, big_line):
        assert response.status_code == 422, response.text[:200]
    assert capped.status_code == 200
    assert capped.json()["end"] - capped.json()["start"] < app.state.settings.max_read_lines
