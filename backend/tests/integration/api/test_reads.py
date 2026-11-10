"""The read endpoints over stored snapshots: each one against GraphStore, every symbol key through
its URL encoding, parameter validation, and snapshots that are missing or not ready."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.graph.model import CodeGraph
from repoatlas.jobs import queue
from repoatlas.jobs.build import dedupe_key
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import begin_snapshot, mark_failed, save_snapshot
from tests.integration.conftest import empty_store
from tests.integration.store.conftest import OWNERS
from tests.integration.store.helpers import (
    FINGERPRINT,
    SHA_A,
    SHA_B,
    build_files,
    build_fixture,
    repo_meta,
    with_owners,
)
from tests.unit.api.helpers import api_settings, client_for, error_of

pytestmark = pytest.mark.anyio

TRICKY: dict[str, bytes] = {
    "src/a b/café.py": "def ünïcode():\n    return 1\n\n\ndef ünïcode():\n    return 2\n".encode(),
    "web/app.py": b"from flask import Flask\n\napp = Flask(__name__)\n\n\n"
    b'@app.route("/users/<id>")\ndef user(id):\n    return helper(id)\n\n\n'
    b"def helper(x):\n    return x\n",
    "web/cart.test.ts": b'describe("cart: 100% > ok", () => {\n  it("adds / removes", () => {\n'
    b"    expect(1).toBe(1);\n  });\n});\n",
    "web/big.py": ("\n".join(f"x{i} = {i}" for i in range(450)) + "\n").encode(),
}
CART = "src/shop/cart.py"


@dataclass(frozen=True)
class Reads:
    app: FastAPI
    store: GraphStore
    py: int
    tricky: int
    pending: int
    pending_job: int
    failed: int
    graphs: dict[int, CodeGraph]


@pytest.fixture(scope="module")
def reads(
    pg_engine: Engine, pg_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Reads]:
    py_built = build_fixture("py_small")
    py_graph = with_owners(py_built.graph, OWNERS)
    tricky = build_files(TRICKY)
    ids: dict[str, int] = {}
    for name, inventory, blobs, graph in (
        ("py-small", py_built.inventory, py_built.blobs, py_graph),
        ("tricky", tricky.inventory, tricky.blobs, tricky.graph),
    ):
        ids[name] = save_snapshot(
            repo_meta("acme", name), SHA_A, FINGERPRINT, inventory, blobs, graph, engine=pg_engine
        )
    pending = begin_snapshot(repo_meta("acme", "later"), SHA_B, FINGERPRINT, engine=pg_engine).id
    job = queue.enqueue(
        "build", snapshot_id=pending, dedupe_key=dedupe_key(pending), engine=pg_engine
    )
    failed = begin_snapshot(repo_meta("acme", "broken"), SHA_B, FINGERPRINT, engine=pg_engine).id
    mark_failed(failed, "OperationalError: [SQL: SELECT secret FROM vault]", engine=pg_engine)
    data_dir = tmp_path_factory.mktemp("reads") / "data"
    app = create_app(api_settings(database_url=pg_url, data_dir=Path(data_dir)))
    try:
        yield Reads(
            app=app,
            store=GraphStore(pg_engine),
            py=ids["py-small"],
            tricky=ids["tricky"],
            pending=pending,
            pending_job=job.id,
            failed=failed,
            graphs={ids["py-small"]: py_graph, ids["tricky"]: tricky.graph},
        )
    finally:
        app.state.engine.dispose()
        empty_store(pg_engine)


def url(snapshot_id: int, rest: str = "") -> str:
    return f"{API_PREFIX}/snapshots/{snapshot_id}{rest}"


def node_url(snapshot_id: int, key: str, rest: str = "") -> str:
    return url(snapshot_id, f"/nodes/{quote(key, safe='')}{rest}")


def dump(model: Any) -> Any:
    return model.model_dump(mode="json")


async def get(reads: Reads, endpoint: str, /, **params: Any) -> Any:
    async with client_for(reads.app) as client:
        return await client.get(endpoint, params=params or None)


# --- the snapshot -------------------------------------------------------------------------


async def test_a_snapshot_shows_its_repo_commit_stats_and_build(reads: Reads) -> None:
    ready = await get(reads, url(reads.py))
    assert ready.status_code == 200
    body = ready.json()
    assert body["repo"] == {
        "host": "github.com",
        "owner": "acme",
        "name": "py-small",
        "default_branch": "main",
    }
    assert (body["commit_sha"], body["config_fingerprint"], body["status"]) == (
        SHA_A,
        FINGERPRINT,
        "ready",
    )
    assert body["stats"]["graph_digest"] == reads.graphs[reads.py].digest()
    assert body["job"] is None and body["error"] is None
    pending = (await get(reads, url(reads.pending))).json()
    assert pending["status"] == "building"
    assert (pending["job"]["id"], pending["job"]["status"]) == (reads.pending_job, "queued")
    failed = (await get(reads, url(reads.failed))).json()
    assert failed["status"] == "failed"
    assert failed["error"].startswith("OperationalError: the build failed")
    assert "secret" not in str(failed)


async def test_unknown_snapshots_are_not_found_everywhere(reads: Reads) -> None:
    missing = 2**62
    for path in (
        url(missing),
        url(missing, "/subgraph"),
        node_url(missing, "dir:."),
        url(missing, "/search?q=x"),
        url(missing, "/code?path=a.py"),
    ):
        response = await get(reads, path)
        assert response.status_code == 404, path
        assert error_of(response)["details"]["resource"] == "snapshot", path


async def test_reads_of_a_snapshot_that_is_not_ready_name_its_build(reads: Reads) -> None:
    for snapshot_id, status, job in (
        (reads.pending, "building", reads.pending_job),
        (reads.failed, "failed", None),
    ):
        for path in (
            url(snapshot_id, "/subgraph"),
            node_url(snapshot_id, "dir:."),
            node_url(snapshot_id, "dir:.", "/neighbors"),
            url(snapshot_id, "/locate?path=a.py&line=1"),
            url(snapshot_id, "/search?q=main"),
            url(snapshot_id, "/path?from=a&to=b"),
            url(snapshot_id, "/code?path=a.py"),
        ):
            response = await get(reads, path)
            assert response.status_code == 409, path
            error = error_of(response)
            assert error["code"] == "snapshot_not_ready"
            assert error["details"]["snapshot_id"] == snapshot_id
            assert error["details"]["status"] == status
            assert error["details"]["job_id"] == job
    failed = error_of(await get(reads, url(reads.failed, "/subgraph")))
    assert failed["details"]["error"].startswith("OperationalError: the build failed")


# --- nodes and keys -----------------------------------------------------------------------


async def test_every_node_is_reachable_by_its_url_encoded_key(reads: Reads) -> None:
    tricky_keys = list(reads.graphs[reads.tricky].nodes)
    assert any("%" in key for key in tricky_keys)  # percent-encoded parts must survive
    assert any(" > " in key and "/" in key.split(":", 2)[2] for key in tricky_keys)
    assert any(not key.isascii() for key in tricky_keys)
    assert any(key.endswith("~2") for key in tricky_keys)
    async with client_for(reads.app) as client:
        for snapshot_id, graph in reads.graphs.items():
            degrees_in: Counter[tuple[str, str]] = Counter()
            degrees_out: Counter[tuple[str, str]] = Counter()
            for edge in graph.edges:
                degrees_out[(edge.src, edge.kind.value)] += 1
                degrees_in[(edge.dst, edge.kind.value)] += 1
            for key, expected in graph.nodes.items():
                response = await client.get(node_url(snapshot_id, key))
                assert response.status_code == 200, (key, response.text)
                body = response.json()
                assert body["node"] == dump(expected), key
                assert body["degree"]["outgoing"] == {
                    kind: count for (src, kind), count in degrees_out.items() if src == key
                }
                assert body["degree"]["incoming"] == {
                    kind: count for (dst, kind), count in degrees_in.items() if dst == key
                }


async def test_nodes_come_with_their_files_owners(reads: Reads) -> None:
    graph = reads.graphs[reads.py]
    symbol = next(key for key, node in graph.nodes.items() if node.path == CART and ":" in key[3:])
    for key in (f"file:{CART}", symbol):
        body = (await get(reads, node_url(reads.py, key))).json()
        assert body["owners"] == [dump(owner) for owner in OWNERS[CART]]
    root = (await get(reads, node_url(reads.py, "dir:."))).json()
    assert root["owners"] == [] and root["degree"]["outgoing"]["contains"] >= 1


async def test_bad_keys(reads: Reads) -> None:
    unknown = await get(reads, node_url(reads.py, "py:nowhere.py:f:function"))
    assert unknown.status_code == 404 and error_of(unknown)["details"] == {"resource": "node"}
    raw_slashes = await get(reads, url(reads.py, f"/nodes/file:{CART}"))  # not encoded
    assert raw_slashes.status_code == 404
    assert error_of(raw_slashes)["details"] == {"resource": "endpoint"}
    for bad in ("%FF%FE", "a%00b"):
        response = await get(reads, url(reads.py, f"/nodes/{bad}"))
        assert response.status_code == 422, bad
        assert error_of(response)["code"] == "invalid_request"
    too_long = await get(reads, node_url(reads.py, "k" * 5_000))
    assert too_long.status_code == 422


# --- traversals ---------------------------------------------------------------------------


async def test_neighbors_match_the_store(reads: Reads) -> None:
    graph = reads.graphs[reads.py]
    file_key = f"file:{CART}"
    for params, (direction, kinds, depth) in (
        ({}, ("out", None, 1)),
        ({"direction": "in", "kinds": ["calls"], "depth": 3}, ("in", ["calls"], 3)),
        (
            {"direction": "both", "kinds": ["imports", "calls"], "depth": 2},
            ("both", ["imports", "calls"], 2),
        ),
    ):
        response = await get(reads, node_url(reads.py, file_key, "/neighbors"), **params)
        assert response.status_code == 200, response.text
        expected = reads.store.neighbors(reads.py, file_key, direction, kinds, depth)  # type: ignore[arg-type]
        assert response.json() == dump(expected)
    assert file_key in graph.nodes
    invalid: list[dict[str, Any]] = [
        {"direction": "up"},
        {"kinds": ["uses"]},
        {"depth": 0},
        {"depth": 21},
        {"depth": "two"},
    ]
    for bad in invalid:
        response = await get(reads, node_url(reads.py, file_key, "/neighbors"), **bad)
        assert response.status_code == 422, bad
        assert error_of(response)["code"] == "invalid_request"


async def test_subgraphs_match_the_store(reads: Reads) -> None:
    default = await get(reads, url(reads.py, "/subgraph"))
    assert default.json() == dump(reads.store.subgraph(reads.py, "dir:.", "dir"))
    for parent, level in (("dir:.", "file"), ("dir:src", "dir"), (f"file:{CART}", "symbol")):
        response = await get(reads, url(reads.py, "/subgraph"), parent=parent, level=level)
        assert response.json() == dump(reads.store.subgraph(reads.py, parent, level))  # type: ignore[arg-type]
    wrong = await get(reads, url(reads.py, "/subgraph"), parent="dir:.", level="symbol")
    assert wrong.status_code == 422 and "needs a file or a symbol" in error_of(wrong)["message"]
    missing = await get(reads, url(reads.py, "/subgraph"), parent="dir:nowhere")
    assert missing.status_code == 404 and error_of(missing)["details"] == {"resource": "node"}
    level = await get(reads, url(reads.py, "/subgraph"), level="module")
    assert level.status_code == 422


async def test_edge_links_match_the_store(reads: Reads) -> None:
    edges = reads.store.subgraph(reads.py, "dir:.", "file").edges
    heaviest = max(edges, key=lambda edge: edge.count)
    params = {"from": heaviest.src, "to": heaviest.dst, "kind": heaviest.kind.value}
    response = await get(reads, url(reads.py, "/links"), **params, limit=3)
    assert response.status_code == 200, response.text
    expected = reads.store.edge_links(reads.py, heaviest.src, heaviest.dst, heaviest.kind, 3)
    assert response.json() == dump(expected)
    assert response.json()["total"] == heaviest.count
    unknown = await get(reads, url(reads.py, "/links"), **{**params, "kind": "owns"})
    assert unknown.status_code == 422
    too_many = await get(reads, url(reads.py, "/links"), **params, limit=10_000)
    assert too_many.status_code == 422
    missing = await get(reads, url(reads.py, "/links"), **{**params, "from": "dir:nowhere"})
    assert missing.status_code == 404
    not_ready = await get(reads, url(reads.pending, "/links"), **params)
    assert not_ready.status_code == 409


async def test_overview_matches_the_store(reads: Reads) -> None:
    response = await get(reads, url(reads.py, "/overview"))
    assert response.status_code == 200, response.text
    assert response.json() == dump(reads.store.overview(reads.py))
    assert response.json()["routes"], "py_small has routes"
    not_ready = await get(reads, url(reads.pending, "/overview"))
    assert not_ready.status_code == 409


async def test_paths_match_the_store_or_are_not_found(reads: Reads) -> None:
    graph = reads.graphs[reads.tricky]
    route = next(key for key in graph.nodes if key.startswith("route:"))
    helper = "py:web/app.py:helper:function"
    response = await get(
        reads,
        url(reads.tricky, "/path"),
        **{"from": route, "to": helper, "kinds": ["routes_to", "calls"]},
    )
    assert response.status_code == 200, response.text
    expected = reads.store.shortest_path(reads.tricky, route, helper, ["routes_to", "calls"])
    assert response.json() == dump(expected)
    assert [node["key"] for node in response.json()["nodes"]] == [
        route,
        "py:web/app.py:user:function",
        helper,
    ]
    disconnected = await get(
        reads,
        url(reads.tricky, "/path"),
        **{"from": helper, "to": "file:web/big.py", "kinds": ["calls"]},
    )
    assert disconnected.status_code == 404
    assert error_of(disconnected)["details"] == {"resource": "path"}
    missing = await get(reads, url(reads.tricky, "/path"), **{"from": helper})
    assert missing.status_code == 422


# --- search, locate, code -----------------------------------------------------------------


async def test_search(reads: Reads) -> None:
    exact = await get(reads, url(reads.tricky, "/search"), q="helper")
    body = exact.json()
    assert body["query"] == "helper"
    assert body["results"][0]["node"]["key"] == "py:web/app.py:helper:function"
    assert body["results"][0]["match"] == "exact"
    assert body == {
        "query": "helper",
        "results": [dump(match) for match in reads.store.find_symbols(reads.tricky, "helper")],
    }
    routes = await get(reads, url(reads.tricky, "/search"), q="users", kinds=["route"])
    assert [r["node"]["kind"] for r in routes.json()["results"]] == ["route"]
    capped = await get(reads, url(reads.tricky, "/search"), q="x", limit=2)
    assert len(capped.json()["results"]) <= 2
    for params in ({"q": ""}, {"q": "x", "limit": 51}, {"q": "x", "kinds": ["module"]}, {}):
        response = await get(reads, url(reads.tricky, "/search"), **params)
        assert response.status_code == 422, params
    blank = await get(reads, url(reads.tricky, "/search"), q="   ")
    assert blank.status_code == 422 and "empty" in error_of(blank)["message"]
    nul = await get(reads, url(reads.tricky, "/search"), q="he\x00lper")
    assert nul.status_code == 422


async def test_locate(reads: Reads) -> None:
    found = await get(reads, url(reads.tricky, "/locate"), path="web/app.py", line=8)
    assert found.status_code == 200 and found.json()["key"] == "py:web/app.py:user:function"
    assert found.json() == dump(reads.store.locate(reads.tricky, "web/app.py", 8))
    top = await get(reads, url(reads.tricky, "/locate"), path="web/app.py", line=1)
    assert top.json()["key"] == "file:web/app.py"
    for params, status in (
        ({"path": "web/app.py", "line": 0}, 422),
        ({"path": "web/app.py", "line": 999}, 422),
        ({"path": "web/nope.py", "line": 1}, 404),
        ({"path": "web/app.py"}, 422),
    ):
        response = await get(reads, url(reads.tricky, "/locate"), **params)
        assert response.status_code == status, params


async def test_code_reads_lines_from_the_stored_text(reads: Reads) -> None:
    response = await get(reads, url(reads.tricky, "/code"), path="web/app.py", start=7, end=8)
    assert response.status_code == 200
    assert response.json() == dump(reads.store.read_code(reads.tricky, "web/app.py", 7, 8))
    assert response.json()["text"] == "def user(id):\n    return helper(id)"
    unicode = await get(reads, url(reads.tricky, "/code"), path="src/a b/café.py", end=1)
    assert unicode.json()["text"] == "def ünïcode():"
    whole = (await get(reads, url(reads.tricky, "/code"), path="web/big.py")).json()
    assert (whole["start"], whole["end"], whole["line_count"]) == (1, 400, 450)  # capped
    tail = (await get(reads, url(reads.tricky, "/code"), path="web/big.py", start=440)).json()
    assert (tail["start"], tail["end"]) == (440, 450)  # clamped to the last line
    capped = (
        await get(reads, url(reads.tricky, "/code"), path="web/big.py", start=10, end=1_000)
    ).json()
    assert (capped["start"], capped["end"]) == (10, 409)
    backwards = await get(reads, url(reads.tricky, "/code"), path="web/big.py", start=9, end=3)
    assert backwards.status_code == 422
    skipped = await get(reads, url(reads.py, "/code"), path="vendor/lib.py")
    assert skipped.status_code == 404
    assert error_of(skipped)["details"] == {"resource": "text"}
    assert "vendored" in error_of(skipped)["message"]
    huge = await get(reads, url(reads.tricky, "/code"), path="web/big.py", start=2**31)
    assert huge.status_code == 422
