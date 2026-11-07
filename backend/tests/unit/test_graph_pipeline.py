from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from repoatlas import pipeline
from repoatlas.cli import app
from repoatlas.config import Settings, get_settings
from repoatlas.ingest import fetch as fetch_module
from repoatlas.ingest.github import GitHubClient
from repoatlas.ingest.source import MemorySourceTree
from repoatlas.pipeline.graph import read_project_files, run_graph
from tests.unit.ingest.gitrepo import OriginRepo, Spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TS_SMALL = FIXTURES / "repos" / "ts_small"


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("REPOATLAS_"):
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)


def test_run_graph_on_a_directory(tmp_path: Path) -> None:
    output = run_graph(str(TS_SMALL), settings(tmp_path))
    assert (output.source, output.commit) == ("dir:ts_small", None)
    assert output.digest == output.graph.digest()
    assert output.graph.owners == {}
    assert output.graph.stats.nodes_by_kind["route"] == 2


@pytest.fixture
def github_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[OriginRepo, str]:
    """A local origin served as if it were github.com/pallets/flask."""
    origin = OriginRepo(tmp_path / "origin.git")
    origin.commit([Spec("src/app.py", b"def main():\n    return run()\n")])
    sha = origin.commit(
        [
            Spec("src/app.py", b"def main():\n    return run()\n\ndef run():\n    pass\n"),
            Spec("pyproject.toml", b"[project]\nname = 'app'\n"),
        ]
    )
    repo_json = json.loads(
        (FIXTURES / "github" / "repo_pallets_flask.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, text=sha)
        return httpx.Response(200, json=repo_json)

    monkeypatch.setattr(
        GitHubClient,
        "from_settings",
        classmethod(lambda cls, _s: cls(transport=httpx.MockTransport(handler))),
    )
    real_source, real_history = fetch_module.fetch_source, fetch_module.fetch_history

    def source_from_origin(clone_url: str, commit: str, dest: Path, **kwargs: Any):
        assert clone_url == "https://github.com/pallets/flask.git"
        return real_source(origin.url, commit, dest, **kwargs)

    def history_from_origin(clone_url: str, commit: str, dest: Path, **kwargs: Any):
        assert clone_url == "https://github.com/pallets/flask.git"
        return real_history(origin.url, commit, dest, **kwargs)

    monkeypatch.setattr(pipeline.graph, "fetch_source", source_from_origin)
    monkeypatch.setattr(pipeline.graph, "fetch_history", history_from_origin)
    return origin, sha


def test_run_graph_on_a_github_repository_reads_owners(
    tmp_path: Path, github_origin: tuple[OriginRepo, str]
) -> None:
    _origin, sha = github_origin
    output = run_graph("https://github.com/pallets/flask", settings(tmp_path))
    assert (output.source, output.commit) == ("github.com/pallets/flask", sha)
    owners = output.graph.owners["src/app.py"]
    assert [(o.author_name, o.commits, o.share) for o in owners] == [("RepoAtlas Tests", 2, 1.0)]
    assert output.graph.stats.history_commits == 2
    calls = {(e.src, e.dst) for e in output.graph.edges if e.kind.value == "calls"}
    assert calls == {("py:src/app.py:main:function", "py:src/app.py:run:function")}
    work = tmp_path / "data" / "work"
    assert work.is_dir() and not any(work.iterdir())  # fetched data is cleaned up


def test_run_graph_without_history(tmp_path: Path, github_origin: tuple[OriginRepo, str]) -> None:
    output = run_graph("https://github.com/pallets/flask", settings(tmp_path), with_history=False)
    assert output.graph.owners == {} and output.graph.stats.history_commits == 0


def test_read_project_files_picks_configs_and_their_bases() -> None:
    files: dict[str, bytes | tuple[str, bytes]] = {
        "pyproject.toml": b"[project]\nname = 'x'\n",
        "web/package.json": b'{"name": "web"}',
        "web/tsconfig.json": b'{"extends": "./configs/base"} // comment',
        "web/configs/base.json": b'{"compilerOptions": {"baseUrl": "."}}',
        "web/tsconfig.build.json": b"{}",
        "web/jsconfig.json": b"{}",
        "web/other.json": b"{}",
        "node_modules/dep/package.json": b"{}",
        "binary/package.json": b"\xff\xfe",
        "big/package.json": b"{" + b" " * 2_000 + b"}",
        "src/app.py": b"x = 1\n",
    }
    with MemorySourceTree(files) as tree:
        project = read_project_files(tree, max_file_bytes=1_000)
    assert sorted(project.files) == [
        "pyproject.toml",
        "web/configs/base.json",
        "web/jsconfig.json",
        "web/package.json",
        "web/tsconfig.build.json",
        "web/tsconfig.json",
    ]


def test_cli_graph_writes_json_and_prints_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOATLAS_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "out" / "ts_small.graph.json"
    result = CliRunner().invoke(app, ["graph", str(TS_SMALL), "--json", str(out), "--stats"])
    assert result.exit_code == 0, result.output
    assert "Nodes:    38 (class 1, dir 6, file 10, function 12, method 4, route 2, test 3)" in (
        result.output
    )
    assert "Edges:" in result.output and "routes_to 2" in result.output
    assert "unresolved 11 (not_found 10, unbound_name 1); external 11; builtin 2" in result.output
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    data = json.loads(raw)
    assert data["source"] == "dir:ts_small"
    assert f"Digest:   {data['digest']}" in result.output
    golden = (FIXTURES / "golden" / "ts_small.graph.json").read_bytes()
    assert raw == golden


def test_cli_graph_summary_and_stdout_json() -> None:
    result = CliRunner().invoke(app, ["graph", str(TS_SMALL)])
    assert result.exit_code == 0
    assert "Graph:    38 nodes, 60 edges; 11 unresolved and 11 external calls" in result.output
    piped = CliRunner().invoke(app, ["graph", str(TS_SMALL), "--json", "-"])
    assert piped.exit_code == 0
    assert '"source": "dir:ts_small"' in piped.stdout
    assert "Digest:" not in piped.stdout


def test_cli_graph_reports_invalid_urls() -> None:
    result = CliRunner().invoke(app, ["graph", "https://gitlab.com/a/b"])
    assert result.exit_code == 1
    assert "Only https://github.com/<owner>/<repo>" in result.output
