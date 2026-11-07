"""Build a snapshot into the store: resolve → fetch → inventory → parse → graph → store (STO-6).

Each step is a plain function of a ``BuildContext``, run in order by ``run_build``, so the steps
can later be scheduled as separate stages. ``resolve`` pins the commit, computes the config
fingerprint, and creates (or finds) the snapshot row; a snapshot that is already ``ready`` is
returned without fetching anything. A failure in any step marks the snapshot ``failed`` with the
error, and the fetched git data is always removed (unless kept for debugging).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field

from pydantic import JsonValue
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.db.engine import create_db_engine
from repoatlas.graph.build import GraphSettings, build_graph, component_versions
from repoatlas.graph.model import CodeGraph
from repoatlas.graph.owners import CommitLog, read_commit_log
from repoatlas.graph.project import ProjectFiles
from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.fetch import Workspace, fetch_history, fetch_source
from repoatlas.ingest.github import GitHubClient, RepoMeta, check_repo_size, parse_repo_url
from repoatlas.ingest.inventory import FileEntry, Inventory, build_inventory
from repoatlas.ingest.source import SourceTree, TreeEntry
from repoatlas.parsing import EXTRACTOR_VERSION
from repoatlas.parsing.models import ParsedFile
from repoatlas.parsing.ts_runtime import grammar_versions
from repoatlas.pipeline import fingerprint
from repoatlas.pipeline.graph import read_project_files
from repoatlas.pipeline.parse import parse_files
from repoatlas.store.loader import begin_snapshot, mark_failed, save_snapshot
from repoatlas.store.models import SnapshotId

_FULL_SHA = re.compile(r"[0-9a-f]{40}")


class TreeBlobs(Mapping[str, bytes]):
    """Blob bytes of ``entries``, read from ``tree`` only when asked for (so the store reads
    just the files it does not have yet)."""

    def __init__(self, tree: SourceTree, entries: Iterable[FileEntry]) -> None:
        self._tree = tree
        self._entries = {
            entry.blob_oid: TreeEntry(entry.path, entry.mode, entry.blob_oid, entry.size)
            for entry in entries
        }

    def __getitem__(self, oid: str) -> bytes:
        return self._tree.read(self._entries[oid])

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class BuildContext:
    """What the steps share. Each step reads earlier steps' fields and fills its own."""

    url: str
    settings: Settings
    engine: Engine
    commit: str | None = None  # pinned by resolve when not requested
    keep_workdir: bool = False
    repo: RepoMeta | None = None
    fingerprint: str | None = None
    fingerprint_parts: dict[str, JsonValue] = field(default_factory=dict)
    snapshot_id: SnapshotId | None = None
    reused: bool = False  # a ready snapshot already existed; the remaining steps are skipped
    history: CommitLog | None = None
    tree: SourceTree | None = None
    inventory: Inventory | None = None
    parsed: list[ParsedFile] | None = None
    project: ProjectFiles | None = None
    graph: CodeGraph | None = None
    timings: dict[str, float] = field(default_factory=dict)
    resources: ExitStack = field(default_factory=ExitStack)

    def need[T](self, value: T | None, what: str) -> T:
        if value is None:
            raise RuntimeError(f"The build has no {what} yet; a step ran out of order.")
        return value


@dataclass(frozen=True)
class Step:
    """One build step: ``settings_keys`` and ``components`` are what its output depends on,
    and both go into the config fingerprint."""

    name: str
    run: Callable[[BuildContext], None]
    settings_keys: tuple[str, ...] = ()
    components: Callable[[], dict[str, JsonValue]] = dict


@dataclass(frozen=True)
class BuildResult:
    snapshot_id: SnapshotId
    reused: bool
    repo: str
    commit: str
    fingerprint: str
    timings: dict[str, float]


def resolve(ctx: BuildContext) -> None:
    """Validate the URL, check the repo on GitHub, pin the commit, and get or create the
    snapshot for (repo, commit, fingerprint)."""
    ref = parse_repo_url(ctx.url)
    if ctx.commit is not None and not _FULL_SHA.fullmatch(ctx.commit):
        raise FetchError("A full 40-character commit SHA is required.")
    with GitHubClient.from_settings(ctx.settings) as github:
        meta = github.get_repo(ref)
        check_repo_size(meta, ctx.settings.max_repo_size_kb)
        ctx.commit = ctx.commit or github.resolve_head(meta.ref, meta.default_branch)
    ctx.repo = meta
    ctx.fingerprint_parts = fingerprint_parts(ctx.settings)
    ctx.fingerprint = fingerprint.compute(ctx.fingerprint_parts)
    ref_row = begin_snapshot(meta, ctx.commit, ctx.fingerprint, engine=ctx.engine)
    ctx.snapshot_id = ref_row.id
    ctx.reused = ref_row.status == "ready"


def fetch(ctx: BuildContext) -> None:
    """Fetch the commit's files and its history (no working tree is ever written)."""
    repo, commit = ctx.need(ctx.repo, "repository"), ctx.need(ctx.commit, "commit")
    settings = ctx.settings
    workspace = ctx.resources.enter_context(
        Workspace(settings.data_dir / "work", keep=ctx.keep_workdir)
    )
    history = fetch_history(
        repo.ref.clone_url,
        commit,
        workspace.path / "history.git",
        depth=settings.history_depth,
        timeout=settings.git_timeout_s,
    )
    ctx.history = read_commit_log(history, settings.owner_commits)
    ctx.tree = ctx.resources.enter_context(
        fetch_source(
            repo.ref.clone_url,
            commit,
            workspace.path / "source.git",
            label=f"github.com/{repo.ref.full_name}",
            timeout=settings.git_timeout_s,
        )
    )


def inventory(ctx: BuildContext) -> None:
    ctx.inventory = build_inventory(
        ctx.need(ctx.tree, "source tree"),
        max_source_files=ctx.settings.max_source_files,
        max_file_bytes=ctx.settings.max_file_bytes,
    )


def parse(ctx: BuildContext) -> None:
    tree = ctx.need(ctx.tree, "source tree")
    ctx.parsed = parse_files(tree, ctx.need(ctx.inventory, "inventory"))
    ctx.project = read_project_files(tree, max_file_bytes=ctx.settings.max_file_bytes)


def graph(ctx: BuildContext) -> None:
    ctx.graph = build_graph(
        ctx.need(ctx.inventory, "inventory"),
        ctx.need(ctx.parsed, "parse results"),
        ctx.history,
        GraphSettings.from_settings(ctx.settings),
        project=ctx.project,
    )


def store(ctx: BuildContext) -> None:
    files = ctx.need(ctx.inventory, "inventory")
    parsed = ctx.need(ctx.parsed, "parse results")
    extra: dict[str, JsonValue] = {
        "source": f"github.com/{ctx.need(ctx.repo, 'repository').ref.full_name}",
        "parse": {
            "files_parsed": len(parsed),
            "files_with_issues": sum(1 for result in parsed if result.issue_count),
            "symbols": sum(len(result.symbols) for result in parsed),
            "calls": sum(len(result.calls) for result in parsed),
        },
        "fingerprint_parts": ctx.fingerprint_parts,
        "timings": dict(ctx.timings),
    }
    saved = save_snapshot(
        ctx.need(ctx.repo, "repository"),
        ctx.need(ctx.commit, "commit"),
        ctx.need(ctx.fingerprint, "fingerprint"),
        files,
        TreeBlobs(ctx.need(ctx.tree, "source tree"), files.indexed),
        ctx.need(ctx.graph, "graph"),
        engine=ctx.engine,
        extra_stats=extra,
    )
    if saved != ctx.snapshot_id:
        raise RuntimeError(f"Saved snapshot {saved}, but the build created {ctx.snapshot_id}.")


def _parse_components() -> dict[str, JsonValue]:
    return {"extractors": EXTRACTOR_VERSION, **grammar_versions()}


def _graph_components() -> dict[str, JsonValue]:
    return dict(component_versions())


BUILD_STEPS: tuple[Step, ...] = (
    Step("resolve", resolve),
    Step("fetch", fetch, ("history_depth",)),
    Step("inventory", inventory, ("max_source_files", "max_file_bytes")),
    Step("parse", parse, ("max_file_bytes",), _parse_components),
    Step("graph", graph, ("owner_commits",), _graph_components),
    Step("store", store),
)


def fingerprint_parts(
    settings: Settings, steps: Sequence[Step] = BUILD_STEPS
) -> dict[str, JsonValue]:
    """What a build's output depends on: every step in order, with its component versions
    and the values of its settings."""
    return {
        "steps": [
            {
                "name": step.name,
                "components": step.components(),
                "settings": {key: getattr(settings, key) for key in step.settings_keys},
            }
            for step in steps
        ]
    }


def config_fingerprint(settings: Settings, steps: Sequence[Step] = BUILD_STEPS) -> str:
    return fingerprint.compute(fingerprint_parts(settings, steps))


def run_build(
    url: str,
    settings: Settings,
    *,
    engine: Engine | None = None,
    commit: str | None = None,
    keep_workdir: bool = False,
    steps: Sequence[Step] = BUILD_STEPS,
) -> BuildResult:
    """Run the steps in order (stopping early when the snapshot was already ready)."""
    owned = engine is None
    ctx = BuildContext(
        url=url,
        settings=settings,
        engine=engine or create_db_engine(settings.database_url),
        commit=commit,
        keep_workdir=keep_workdir,
    )
    try:
        with ctx.resources:
            for step in steps:
                if ctx.reused:
                    break
                started = time.perf_counter()
                step.run(ctx)
                ctx.timings[step.name] = round(time.perf_counter() - started, 3)
    except Exception as error:
        if ctx.snapshot_id is not None and not ctx.reused:
            mark_failed(ctx.snapshot_id, error, engine=ctx.engine)
        raise
    finally:
        if owned:
            ctx.engine.dispose()
    return BuildResult(
        snapshot_id=ctx.need(ctx.snapshot_id, "snapshot"),
        reused=ctx.reused,
        repo=ctx.need(ctx.repo, "repository").ref.full_name,
        commit=ctx.need(ctx.commit, "commit"),
        fingerprint=ctx.need(ctx.fingerprint, "fingerprint"),
        timings=ctx.timings,
    )


def build_snapshot(
    url: str,
    settings: Settings,
    *,
    engine: Engine | None = None,
    commit: str | None = None,
    keep_workdir: bool = False,
) -> SnapshotId:
    """Build (or reuse) the snapshot of a public GitHub repository and return its ID."""
    result = run_build(url, settings, engine=engine, commit=commit, keep_workdir=keep_workdir)
    return result.snapshot_id
