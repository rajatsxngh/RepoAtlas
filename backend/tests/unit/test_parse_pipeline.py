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
from repoatlas.ingest.errors import RepoTooLarge
from repoatlas.ingest.github import GitHubClient
from repoatlas.pipeline.parse import run_parse
from tests.unit.ingest.gitrepo import OriginRepo, Spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PY_SMALL = FIXTURES / "repos" / "py_small"


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


def test_run_parse_on_a_directory(tmp_path: Path) -> None:
    output = run_parse(str(PY_SMALL), settings(tmp_path))
    assert output.source == "dir:py_small"
    assert output.commit is None
    assert output.stats.files_parsed == 7
    assert len(output.digest) == 64


@pytest.fixture
def github_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[OriginRepo, str]:
    """A local origin served as if it were github.com/pallets/flask."""
    origin = OriginRepo(tmp_path / "origin.git")
    sha = origin.commit(
        [
            Spec("src/app.py", b"def main():\n    return run()\n"),
            Spec("node_modules/x/index.js", b"module.exports = 1;\n"),
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
    real_fetch = fetch_module.fetch_source

    def fetch_from_origin(clone_url: str, commit: str, dest: Path, **kwargs: Any):
        assert clone_url == "https://github.com/pallets/flask.git"
        return real_fetch(origin.url, commit, dest, **kwargs)

    monkeypatch.setattr(pipeline.parse, "fetch_source", fetch_from_origin)
    return origin, sha


def test_run_parse_on_a_github_repository(
    tmp_path: Path, github_origin: tuple[OriginRepo, str]
) -> None:
    _origin, sha = github_origin
    output = run_parse("https://github.com/pallets/flask", settings(tmp_path))
    assert output.source == "github.com/pallets/flask"
    assert output.commit == sha
    assert [f.path for f in output.files] == ["src/app.py"]
    assert output.inventory.stats.skipped == {"vendored": 1}
    work = tmp_path / "data" / "work"
    assert work.is_dir() and not any(work.iterdir())  # fetched data is cleaned up


def test_run_parse_enforces_the_repo_size_cap(
    tmp_path: Path, github_origin: tuple[OriginRepo, str]
) -> None:
    with pytest.raises(RepoTooLarge):
        run_parse("https://github.com/pallets/flask", settings(tmp_path, max_repo_size_kb=1_000))


def test_cli_parse_writes_lf_json_and_prints_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOATLAS_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "out" / "py_small.json"
    result = CliRunner().invoke(app, ["parse", str(PY_SMALL), "--json", str(out)])
    assert result.exit_code == 0, result.output
    assert "Files:    7 parsed (py 7)" in result.output
    assert "Digest:" in result.output
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw)["source"] == "dir:py_small"


def test_cli_parse_to_stdout_keeps_the_summary_out_of_the_json(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["parse", str(PY_SMALL), "--json", "-"])
    assert result.exit_code == 0
    assert '"source": "dir:py_small"' in result.stdout
    assert "Digest:" not in result.stdout


def test_cli_parse_reports_invalid_urls() -> None:
    result = CliRunner().invoke(app, ["parse", "https://gitlab.com/a/b"])
    assert result.exit_code == 1
    assert "Only https://github.com/<owner>/<repo>" in result.output
