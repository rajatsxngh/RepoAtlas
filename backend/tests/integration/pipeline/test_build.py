"""build_snapshot and the build/query CLI (T7), against a local origin served as GitHub."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from repoatlas import pipeline
from repoatlas.cli import app
from repoatlas.config import Settings, get_settings
from repoatlas.ingest import fetch as fetch_module
from repoatlas.ingest.errors import FetchError, InvalidRepoUrl
from repoatlas.ingest.github import GitHubClient
from repoatlas.pipeline.build import BUILD_STEPS, BuildContext, Step, build_snapshot, run_build
from repoatlas.pipeline.graph import run_graph
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import load_graph
from tests.unit.ingest.gitrepo import OriginRepo, Spec

pytestmark = pytest.mark.usefixtures("clean_store")

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
URL = "https://github.com/pallets/flask"
CLONE_URL = "https://github.com/pallets/flask.git"
APP = (
    b"from shop.util import helper\n\n\n"
    b"def main():\n    return run()\n\n\n"
    b"def run():\n    return helper()\n"
)
UTIL = b"def helper():\n    return 1\n"
TEST = b"from shop.app import main\n\n\ndef test_main():\n    assert main() == 1\n"
HELPER = "py:src/shop/util.py:helper:function"
FILES = [
    Spec("pyproject.toml", b"[project]\nname = 'shop'\n"),
    Spec("src/shop/__init__.py", b""),
    Spec("src/shop/app.py", APP),
    Spec("src/shop/util.py", UTIL),
    Spec("tests/test_app.py", TEST),
]


class Origin:
    """A local bare repository that the mocked GitHub API and the patched fetches point to."""

    def __init__(self, path: Path) -> None:
        self.repo = OriginRepo(path)
        self.repo.commit([Spec("src/shop/app.py", b"def main():\n    pass\n")], "first")
        self.head = self.repo.commit(FILES, "second")
        self.fetches = 0

    def commit(self, changes: list[Spec]) -> str:
        changed = {spec.path for spec in changes}
        self.head = self.repo.commit([s for s in FILES if s.path not in changed] + changes)
        return self.head


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    served = Origin(tmp_path / "origin.git")
    repo_json = json.loads((FIXTURES / "github" / "repo_pallets_flask.json").read_text("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, text=served.head)
        return httpx.Response(200, json=repo_json)

    monkeypatch.setattr(
        GitHubClient,
        "from_settings",
        classmethod(lambda cls, _s: cls(transport=httpx.MockTransport(handler))),
    )
    real_source, real_history = fetch_module.fetch_source, fetch_module.fetch_history

    def source(clone_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        assert clone_url == CLONE_URL
        served.fetches += 1
        return real_source(served.repo.url, commit, dest, **kwargs)

    def history(clone_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        assert clone_url == CLONE_URL
        return real_history(served.repo.url, commit, dest, **kwargs)

    for module in (pipeline.build, pipeline.graph):
        monkeypatch.setattr(module, "fetch_source", source)
        monkeypatch.setattr(module, "fetch_history", history)
    return served


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", database_url=pg_url)


def status(engine: Engine, snapshot_id: int) -> tuple[str, str | None]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, error FROM snapshots WHERE id = :s"), {"s": snapshot_id}
        ).one()
    return row.status, row.error


def workdir_is_empty(settings: Settings) -> bool:
    work = settings.data_dir / "work"
    return not work.exists() or not any(work.iterdir())


def test_build_stores_the_same_graph_as_run_graph(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    result = run_build(URL, settings, engine=pg_engine)
    assert (result.repo, result.commit, result.reused) == ("pallets/flask", origin.head, False)
    assert list(result.timings) == [step.name for step in BUILD_STEPS]
    expected = run_graph(URL, settings)
    loaded = load_graph(result.snapshot_id, engine=pg_engine)
    assert loaded.digest() == expected.digest
    assert loaded.owners["src/shop/app.py"][0].commits == 2  # history reached the store
    info = GraphStore(pg_engine).snapshot(result.snapshot_id)
    assert (info.status, info.commit_sha, info.config_fingerprint) == (
        "ready",
        origin.head,
        result.fingerprint,
    )
    assert info.stats["graph_digest"] == expected.digest
    assert info.stats["source"] == "github.com/pallets/flask"
    timings = info.stats["timings"]
    assert isinstance(timings, dict) and set(timings) == {
        "resolve",
        "fetch",
        "inventory",
        "parse",
        "graph",
    }
    assert info.stats["fingerprint_parts"] == pipeline.build.fingerprint_parts(settings)
    assert workdir_is_empty(settings)


def test_a_second_build_reuses_the_snapshot_without_fetching(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    first = build_snapshot(URL, settings, engine=pg_engine)
    assert origin.fetches == 1
    again = run_build(URL, settings, engine=pg_engine)
    assert (again.snapshot_id, again.reused, origin.fetches) == (first, True, 1)
    assert list(again.timings) == ["resolve"]
    other = run_build(
        URL, Settings(**{**settings.model_dump(), "owner_commits": 1}), engine=pg_engine
    )
    assert other.snapshot_id != first and not other.reused  # another fingerprint


def test_a_new_commit_stores_only_the_changed_text(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    first = build_snapshot(URL, settings, engine=pg_engine)
    origin.commit([Spec("src/shop/util.py", UTIL + b"\n\ndef other():\n    return 2\n")])
    second = build_snapshot(URL, settings, engine=pg_engine)
    assert second != first
    blobs = GraphStore(pg_engine).snapshot(second).stats["blobs"]
    assert isinstance(blobs, dict) and (blobs["stored"], blobs["shared"]) == (1, 3)
    pinned = build_snapshot(URL, settings, engine=pg_engine, commit=origin.repo.head)
    assert pinned == second


def test_a_failing_step_marks_the_snapshot_failed_and_a_retry_succeeds(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    def broken(ctx: BuildContext) -> None:
        raise RuntimeError("parser exploded")

    steps = tuple(Step("parse", broken) if s.name == "parse" else s for s in BUILD_STEPS)
    with pytest.raises(RuntimeError, match="parser exploded"):
        run_build(URL, settings, engine=pg_engine, steps=steps)
    assert workdir_is_empty(settings)
    with pg_engine.connect() as connection:
        snapshot_id = connection.execute(text("SELECT id FROM snapshots")).scalar_one()
    assert status(pg_engine, snapshot_id) == ("failed", "RuntimeError: parser exploded")
    assert build_snapshot(URL, settings, engine=pg_engine) == snapshot_id
    assert status(pg_engine, snapshot_id) == ("ready", None)


def test_fetch_errors_are_recorded(origin: Origin, settings: Settings, pg_engine: Engine) -> None:
    origin.head = "0" * 40  # GitHub reports a commit the origin does not have
    with pytest.raises(FetchError):
        build_snapshot(URL, settings, engine=pg_engine)
    with pg_engine.connect() as connection:
        row = connection.execute(text("SELECT status, error FROM snapshots")).one()
    assert row.status == "failed" and row.error.startswith("FetchError")
    assert workdir_is_empty(settings)


def test_invalid_urls_and_commits_never_create_a_snapshot(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    with pytest.raises(InvalidRepoUrl):
        build_snapshot("https://gitlab.com/a/b", settings, engine=pg_engine)
    for commit in ("abc123", origin.head.upper(), origin.head + "0"):
        with pytest.raises(FetchError, match="40-character"):
            build_snapshot(URL, settings, engine=pg_engine, commit=commit)
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM snapshots")).scalar_one() == 0


@pytest.fixture
def cli_env(tmp_path: Path, pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("REPOATLAS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", pg_url)
    monkeypatch.setenv("REPOATLAS_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def invoke(*args: str) -> Any:
    result = CliRunner().invoke(app, list(args))
    return result


@pytest.mark.usefixtures("cli_env")
def test_cli_build_then_query_end_to_end(origin: Origin) -> None:
    built = invoke("build", URL)
    assert built.exit_code == 0, built.output
    match = re.search(r"Snapshot: (\d+) \(ready, built\)", built.output)
    assert match, built.output
    snapshot = match.group(1)
    assert f"Repo:     github.com/pallets/flask @ {origin.head}" in built.output
    assert "Steps:    resolve" in built.output

    def query(*args: str) -> Any:
        result = invoke("query", *args, "--snapshot", snapshot)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    callers = query("callers", HELPER, "--depth", "3")
    assert {hop["node"]["key"]: hop["depth"] for hop in callers["nodes"]} == {
        "py:src/shop/app.py:run:function": 1,
        "py:src/shop/app.py:main:function": 2,
        "py:tests/test_app.py:test_main:test": 3,
    }
    assert query("callees", "py:src/shop/app.py:main:function")["nodes"][0]["depth"] == 1
    assert query("node", HELPER)["name"] == "helper"
    assert query("search", "helpr")[0]["node"]["key"] == HELPER
    assert query("search", "main", "--kinds", "function,test")[0]["match"] == "exact"
    assert query("locate", "src/shop/app.py", "9")["key"] == "py:src/shop/app.py:run:function"
    assert query("code", "src/shop/util.py", "1", "2")["text"] == "def helper():\n    return 1"
    path = query("path", "py:tests/test_app.py:test_main:test", HELPER)
    assert [node["name"] for node in path["nodes"]] == ["test_main", "main", "run", "helper"]
    assert path["directed"] is True
    subgraph = query("subgraph", "dir:.")
    assert [node["key"] for node in subgraph["nodes"]] == ["dir:src", "dir:tests"]
    assert {edge["kind"] for edge in subgraph["edges"]} >= {"calls", "imports", "tested_by"}
    assert query("subgraph", "file:src/shop/app.py", "--level", "symbol")["nodes"][0]["kind"] == (
        "function"
    )
    neighbors = query("neighbors", "file:src/shop/app.py", "--direction", "both", "-k", "imports")
    assert {hop["node"]["key"] for hop in neighbors["nodes"]} == {
        "file:src/shop/util.py",
        "file:tests/test_app.py",
    }
    assert query("owners", "src/shop/app.py")[0]["commits"] == 2
    assert query("unresolved", "nothing") == []
    assert query("snapshot")["status"] == "ready"

    again = invoke("build", URL)
    assert again.exit_code == 0 and f"Snapshot: {snapshot} (ready, reused" in again.output


@pytest.mark.usefixtures("cli_env", "origin")
def test_cli_query_errors() -> None:
    built = invoke("build", URL)
    snapshot = re.search(r"Snapshot: (\d+)", built.output).group(1)  # type: ignore[union-attr]
    for args, message in (
        (["node", "py:nowhere.py:f:function"], "has no node"),
        (["callers", "py:nowhere.py:f:function"], "has no node"),
        (["code", "../etc/passwd", "1", "2"], "has no file"),
        (["subgraph", "dir:.", "--level", "module"], "level must be one of"),
        (["neighbors", HELPER, "--direction", "up"], "direction must be one of"),
        (["path", "dir:src", HELPER, "-k", "calls"], "No path between"),
        (["search", "x", "--kinds", "module"], "module"),
    ):
        result = invoke("query", *args, "--snapshot", snapshot)
        assert result.exit_code == 1, (args, result.output)
        assert message in result.output, (args, result.output)
    missing = invoke("query", "snapshot", "--snapshot", "999999")
    assert missing.exit_code == 1 and "does not exist" in missing.output
    bad = invoke("build", "https://example.com/a/b")
    assert bad.exit_code == 1 and "Error:" in bad.output
