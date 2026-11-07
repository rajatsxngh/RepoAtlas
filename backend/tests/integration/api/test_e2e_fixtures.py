"""The web app's end-to-end fixture repositories build through the real pipeline, and repo text
full of HTML and script comes back over the API byte for byte (the web app must show it as
text)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from repoatlas.api.app import API_PREFIX
from repoatlas.config import Settings
from repoatlas.ingest.github import GitHubClient
from tests.e2e.fixtures import (
    OWNER,
    XSS_DIR,
    XSS_FILE,
    XSS_OWNER,
    XSS_ROUTE,
    XSS_TEST_FILE,
    XSS_TS_FILE,
    build_repos,
    github_transport,
    patch_fetches,
)
from tests.integration.api.conftest import run_worker
from tests.unit.api.helpers import client_for

pytestmark = pytest.mark.anyio


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repos = build_repos(tmp_path / "origins")
    transport = github_transport(repos)
    monkeypatch.setattr(
        GitHubClient, "from_settings", classmethod(lambda cls, _s: cls(transport=transport))
    )
    patch_fetches(repos, monkeypatch.setattr)


async def test_the_fixture_repositories_build_and_keep_their_text(
    app: FastAPI, served: None, settings: Settings, pg_engine: Engine
) -> None:
    async with client_for(app) as client:
        snapshots = {}
        for name in ("shop", "xss"):
            accepted = await client.post(
                f"{API_PREFIX}/repos", json={"url": f"https://github.com/{OWNER}/{name}"}
            )
            assert accepted.status_code == 202, accepted.text
            run_worker(settings, pg_engine)
            snapshots[name] = accepted.json()["snapshot_id"]

        shop = f"{API_PREFIX}/snapshots/{snapshots['shop']}"
        root = (await client.get(f"{shop}/subgraph")).json()
        assert {node["key"] for node in root["nodes"]} >= {"dir:src", "dir:tests"}
        cart = (await client.get(f"{shop}/nodes/{quote('file:src/shop/cart.py', safe='')}")).json()
        assert [owner["author_name"] for owner in cart["owners"]] == ["Avery Chen", "Sam Okafor"]
        found = (
            await client.get(
                f"{shop}/path",
                params={
                    "from": "py:tests/test_cart.py:test_add_rounds_prices:test",
                    "to": "py:src/shop/utils/helpers.py:round_money:function",
                },
            )
        ).json()
        assert found["nodes"][-1]["name"] == "round_money"

        xss = f"{API_PREFIX}/snapshots/{snapshots['xss']}"
        root = (await client.get(f"{xss}/subgraph")).json()
        assert {node["name"] for node in root["nodes"]} >= {XSS_DIR, XSS_TS_FILE}
        inner = (await client.get(f"{xss}/subgraph", params={"parent": f"dir:{XSS_DIR}"})).json()
        assert [node["name"] for node in inner["nodes"]] == [XSS_FILE]
        file_key = inner["nodes"][0]["key"]
        detail = (await client.get(f"{xss}/nodes/{quote(file_key, safe='')}")).json()
        assert [owner["author_name"] for owner in detail["owners"]] == [XSS_OWNER]
        symbols = (
            await client.get(f"{xss}/subgraph", params={"parent": file_key, "level": "symbol"})
        ).json()
        names = {node["name"] for node in symbols["nodes"]}
        assert {"render", "helper", f"GET {XSS_ROUTE}"} <= names
        code = (await client.get(f"{xss}/code", params={"path": f"{XSS_DIR}/{XSS_FILE}"})).json()
        assert code["text"].startswith("# <script>alert('comment')</script>")
        ts_file = next(node for node in root["nodes"] if node["name"] == XSS_TEST_FILE)
        cases = (
            await client.get(
                f"{xss}/subgraph", params={"parent": ts_file["key"], "level": "symbol"}
            )
        ).json()
        test_names = [node["name"] for node in cases["nodes"] if node["kind"] == "test"]
        assert any("<script>alert('test')</script>" in name for name in test_names), test_names

        private = await client.post(
            f"{API_PREFIX}/repos", json={"url": f"https://github.com/{OWNER}/private"}
        )
        assert private.status_code == 403
