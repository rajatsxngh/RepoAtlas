"""A local git origin served as github.com/pallets/flask, for builds that never touch the network.

``serve_origin`` mocks the GitHub API (repository metadata and the default branch's head) and
points every fetch at a local bare repository. Tests can change what GitHub says
(``repo_json``), answer a path with a canned response (``responses``), or commit new files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from repoatlas.ingest import fetch as fetch_module
from repoatlas.ingest.github import GitHubClient
from repoatlas.pipeline import build as build_module
from repoatlas.pipeline import graph as graph_module
from tests.unit.ingest.gitrepo import OriginRepo, Spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
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


def github_json(name: str) -> Any:
    return json.loads((FIXTURES / "github" / name).read_text("utf-8"))


class Origin:
    """A local bare repository that the mocked GitHub API and the patched fetches point to."""

    def __init__(self, path: Path) -> None:
        self.repo = OriginRepo(path)
        self.repo.commit([Spec("src/shop/app.py", b"def main():\n    pass\n")], "first")
        self.head = self.repo.commit(FILES, "second")
        self.fetches = 0
        self.history_fetches = 0
        self.repo_json: dict[str, Any] = github_json("repo_pallets_flask.json")
        self.responses: dict[str, httpx.Response] = {}
        self.api_paths: list[str] = []

    def commit(self, changes: list[Spec]) -> str:
        changed = {spec.path for spec in changes}
        self.head = self.repo.commit([s for s in FILES if s.path not in changed] + changes)
        return self.head

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.api_paths.append(request.url.path)
        canned = self.responses.get(request.url.path)
        if canned is not None:
            return canned
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, text=self.head)
        return httpx.Response(200, json=self.repo_json)


def serve_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    served = Origin(tmp_path / "origin.git")
    monkeypatch.setattr(
        GitHubClient,
        "from_settings",
        classmethod(lambda cls, _s: cls(transport=httpx.MockTransport(served.answer))),
    )
    real_source, real_history = fetch_module.fetch_source, fetch_module.fetch_history

    def source(clone_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        assert clone_url == CLONE_URL
        served.fetches += 1
        return real_source(served.repo.url, commit, dest, **kwargs)

    def history(clone_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        assert clone_url == CLONE_URL
        served.history_fetches += 1
        return real_history(served.repo.url, commit, dest, **kwargs)

    for module in (build_module, graph_module):
        monkeypatch.setattr(module, "fetch_source", source)
        monkeypatch.setattr(module, "fetch_history", history)
    return served
