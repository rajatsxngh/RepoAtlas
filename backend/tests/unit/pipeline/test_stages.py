"""Running stages: weighted progress, checks between stages, and resuming from checkpoints."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from repoatlas.config import Settings
from repoatlas.db.engine import create_db_engine
from repoatlas.ingest import fetch as fetch_module
from repoatlas.ingest.github import RepoMeta, RepoRef
from repoatlas.pipeline import build as build_module
from repoatlas.pipeline.build import BUILTIN_STAGES, build_dir, open_build_dir
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import Stage, run_stages
from tests.unit.api.helpers import NOWHERE_DB
from tests.unit.ingest.gitrepo import OriginRepo, Spec

APP = b"from shop.util import helper\n\n\ndef main():\n    return helper()\n"
UTIL = b"def helper():\n    return 1\n"
FILES = [
    Spec("pyproject.toml", b"[project]\nname = 'shop'\n"),
    Spec("src/shop/__init__.py", b""),
    Spec("src/shop/app.py", APP),
    Spec("src/shop/util.py", UTIL),
    Spec("tests/test_app.py", b"from shop.app import main\n\n\ndef test_main():\n    main()\n"),
]
PRE_STORE = BUILTIN_STAGES[: [stage.name for stage in BUILTIN_STAGES].index("store")]


class Stopped(Exception):
    pass


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", database_url=NOWHERE_DB)


def context(settings: Settings, **fields: Any) -> BuildContext:
    return BuildContext(
        url="https://github.com/acme/shop",
        settings=settings,
        engine=create_db_engine(NOWHERE_DB),
        **fields,
    )


def test_progress_is_weighted_by_stage(settings: Settings) -> None:
    def half(ctx: BuildContext) -> None:
        ctx.progress(0.5)
        ctx.progress(7)  # clamped

    reports: list[tuple[str, float]] = []
    ctx = context(settings)
    stages = [Stage("a", 1, (), half, weight=1), Stage("b", 1, (), half, weight=3)]
    run_stages(ctx, stages, report=lambda stage, value: reports.append((stage, value)))
    assert reports == [
        ("a", 0.0),
        ("a", 0.125),
        ("a", 0.25),
        ("a", 0.25),
        ("b", 0.25),
        ("b", 0.625),
        ("b", 1.0),
        ("b", 1.0),
    ]
    assert set(ctx.timings) == {"a", "b"} and ctx.on_progress is None
    run_stages(ctx, stages)  # no reporter: progress calls are harmless


def test_checks_run_before_every_stage(settings: Settings) -> None:
    ran: list[str] = []
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise Stopped

    def record(name: str) -> Stage:
        return Stage(name, 1, (), lambda _ctx: ran.append(name))

    ctx = context(settings)
    with pytest.raises(Stopped):
        run_stages(ctx, [record("a"), record("b"), record("c")], check=check)
    assert ran == ["a"] and list(ctx.timings) == ["a"] and ctx.on_progress is None


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A local origin standing in for github.com/acme/shop; counts fetches."""
    repo = OriginRepo(tmp_path / "origin.git")
    head = repo.commit(FILES)
    counts: dict[str, Any] = {"source": 0, "history": 0, "head": head}
    real_source, real_history = fetch_module.fetch_source, fetch_module.fetch_history

    def source(_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        counts["source"] += 1
        return real_source(repo.url, commit, dest, **kwargs)

    def history(_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        counts["history"] += 1
        return real_history(repo.url, commit, dest, **kwargs)

    monkeypatch.setattr(build_module, "fetch_source", source)
    monkeypatch.setattr(build_module, "fetch_history", history)
    yield counts


def checkpointed(settings: Settings, head: str) -> BuildContext:
    meta = RepoMeta(
        ref=RepoRef("acme", "shop"),
        default_branch="main",
        private=False,
        size_kb=1,
        html_url="https://github.com/acme/shop",
        archived=False,
        fork=False,
    )
    ctx = context(settings, repo=meta, commit=head, fingerprint="f" * 64)
    ctx.workdir = open_build_dir(ctx)
    return ctx


def test_a_second_run_resumes_from_checkpoints(settings: Settings, origin: dict[str, Any]) -> None:
    first = checkpointed(settings, origin["head"])
    with first.resources:
        run_stages(first, PRE_STORE)
    assert first.resumed == []
    assert first.workdir is not None
    assert first.workdir == build_dir(settings, "ACME/shop", origin["head"], "f" * 64)
    names = {path.name for path in first.workdir.iterdir()}
    assert names == {
        "build.json",
        "source.git",
        "fetch.json",
        "inventory.json",
        "parse.json",
        "graph.json",
    }  # history.git is gone once its commit log is saved
    assert (origin["source"], origin["history"]) == (1, 1)

    second = checkpointed(settings, origin["head"])
    with second.resources:
        run_stages(second, PRE_STORE)
    assert second.resumed == ["fetch", "inventory", "parse", "graph"]
    assert (origin["source"], origin["history"]) == (1, 1)  # nothing fetched again
    assert second.graph is not None and first.graph is not None
    assert second.graph.digest() == first.graph.digest()
    assert second.inventory == first.inventory and second.parsed == first.parsed
    assert second.history == first.history and second.project == first.project
    with second.resources:  # the reopened source tree reads the same bytes
        assert second.tree is not None
        entry = next(e for e in second.tree.entries() if e.path == "src/shop/util.py")
        assert second.tree.read(entry) == UTIL


def test_damaged_checkpoints_are_rebuilt(settings: Settings, origin: dict[str, Any]) -> None:
    first = checkpointed(settings, origin["head"])
    with first.resources:
        run_stages(first, PRE_STORE)
    assert first.workdir is not None and first.graph is not None
    (first.workdir / "parse.json").write_text("{not json", encoding="utf-8")
    (first.workdir / "graph.json").unlink()
    again = checkpointed(settings, origin["head"])
    with again.resources:
        run_stages(again, PRE_STORE)
    assert again.resumed == ["fetch", "inventory"]
    assert again.graph is not None and again.graph.digest() == first.graph.digest()
    assert (first.workdir / "parse.json").read_bytes().startswith(b"{")


def test_an_incomplete_fetch_is_fetched_again(settings: Settings, origin: dict[str, Any]) -> None:
    ctx = checkpointed(settings, origin["head"])
    assert ctx.workdir is not None
    (ctx.workdir / "source.git").mkdir()  # an attempt died mid-fetch, before fetch.json
    with ctx.resources:
        run_stages(ctx, PRE_STORE[:1])
    assert ctx.resumed == [] and origin["source"] == 1
    other_commit = checkpointed(settings, origin["head"])
    assert other_commit.workdir is not None
    marker = other_commit.workdir / "fetch.json"
    marker.write_text(marker.read_text(encoding="utf-8").replace(origin["head"], "0" * 40))
    with other_commit.resources:
        run_stages(other_commit, PRE_STORE[:1])
    assert other_commit.resumed == [] and origin["source"] == 2


def test_without_a_work_directory_nothing_is_kept(
    settings: Settings, origin: dict[str, Any]
) -> None:
    ctx = checkpointed(settings, origin["head"])
    ctx.workdir = None
    with ctx.resources:
        run_stages(ctx, PRE_STORE)
    assert ctx.graph is not None and ctx.resumed == []
    work = settings.data_dir / "work"
    assert not work.exists() or not any(work.iterdir())
