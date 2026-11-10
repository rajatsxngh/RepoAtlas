"""Build a snapshot into the store (STO-6): resolve, then run the registered stages.

``resolve`` validates the URL, checks the repository on GitHub, pins the commit, computes the
config fingerprint over the registered stages, and gets or creates the snapshot; a snapshot that
is already ``ready`` is reused without fetching anything. The built-in stages, registered here,
are ``fetch`` → ``inventory`` → ``parse`` → ``graph`` → ``store`` → ``publish``; other stages
that work on the stored snapshot (chunking, embeddings) register between ``store`` and
``publish``. ``store`` saves the snapshot as ``building`` and ``publish`` makes it ``ready``, so
a snapshot is never ready (and reused) before every stage has run (decision 0016).
``run_build`` runs them in process (``repoatlas build``); the worker runs the same stages as a
background job.

Stages keep their output in the build's work directory (``build_dir``, one per repo, commit,
and fingerprint): the fetched commit, and the inventory, parse results, and graph as JSON
checkpoints. A retried build finds them and resumes where the last attempt stopped, and
``store`` returns at once for a snapshot that is already ready. A failure marks the snapshot
``failed``, and the work directory is removed once the build is over (unless kept for
debugging).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import Engine

from repoatlas.canonical import digest, pretty_json, write_text_lf
from repoatlas.config import Settings
from repoatlas.db.engine import create_db_engine
from repoatlas.graph.build import GraphSettings, build_graph, component_versions
from repoatlas.graph.model import CodeGraph
from repoatlas.graph.owners import CommitLog, read_commit_log
from repoatlas.graph.project import ProjectFiles
from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.fetch import Workspace, fetch_history, fetch_source, remove_tree
from repoatlas.ingest.git import run_git
from repoatlas.ingest.github import GitHubClient, RepoMeta, check_repo_size, parse_repo_url
from repoatlas.ingest.inventory import FileEntry, Inventory, build_inventory
from repoatlas.ingest.source import GitSourceTree, SourceTree, TreeEntry
from repoatlas.logging import get_logger
from repoatlas.parsing import EXTRACTOR_VERSION
from repoatlas.parsing.models import ParsedFile
from repoatlas.parsing.ts_runtime import grammar_versions
from repoatlas.pipeline import fingerprint
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.graph import read_project_files
from repoatlas.pipeline.parse import parse_files
from repoatlas.pipeline.stages import (
    Stage,
    fingerprint_parts,
    register_builtin,
    registered_stages,
    run_stages,
)
from repoatlas.store.loader import begin_snapshot, mark_failed, mark_ready, save_snapshot
from repoatlas.store.models import SnapshotId

__all__ = [
    "BUILTIN_STAGES",
    "BuildContext",
    "BuildResult",
    "Target",
    "TreeBlobs",
    "build_dir",
    "build_snapshot",
    "fetch",
    "graph",
    "inventory",
    "open_build_dir",
    "parse",
    "publish",
    "resolve",
    "resolve_target",
    "run_build",
    "store",
]

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
SOURCE_DIR = "source.git"
HISTORY_DIR = "history.git"

log = get_logger(__name__)


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


@dataclass(frozen=True)
class Target:
    """What a build request resolves to: the repository, the pinned commit, and the config
    fingerprint of the pipeline that will build it."""

    repo: RepoMeta
    commit: str
    fingerprint: str
    fingerprint_parts: dict[str, JsonValue]


@dataclass(frozen=True)
class BuildResult:
    snapshot_id: SnapshotId
    reused: bool
    repo: str
    commit: str
    fingerprint: str
    timings: dict[str, float]


class FetchCheckpoint(BaseModel):
    """A finished fetch: the commit in ``source.git`` and the commit log read from history."""

    model_config = ConfigDict(frozen=True)

    commit: str
    history: CommitLog


class ParseCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    files: list[ParsedFile]
    project: ProjectFiles


# --- resolve ------------------------------------------------------------------------------


def resolve_target(
    url: str,
    settings: Settings,
    *,
    commit: str | None = None,
    stages: Sequence[Stage] | None = None,
    github: GitHubClient | None = None,
) -> Target:
    """Validate the URL, check the repository on GitHub (public, under the size cap), pin the
    commit (the default branch's head unless given), and compute the fingerprint."""
    ref = parse_repo_url(url)
    if commit is not None and not _FULL_SHA.fullmatch(commit):
        raise FetchError("A full 40-character commit SHA is required.")
    client = github or GitHubClient.from_settings(settings)
    try:
        meta = client.get_repo(ref)
        check_repo_size(meta, settings.max_repo_size_kb)
        pinned = commit or client.resolve_head(meta.ref, meta.default_branch)
    finally:
        if github is None:
            client.close()
    parts = fingerprint_parts(settings, stages)
    return Target(
        repo=meta, commit=pinned, fingerprint=fingerprint.compute(parts), fingerprint_parts=parts
    )


def resolve(ctx: BuildContext, stages: Sequence[Stage] | None = None) -> None:
    """Resolve the build's target and get or create its snapshot (``building`` unless it is
    already ``ready``, which sets ``ctx.reused``)."""
    target = resolve_target(ctx.url, ctx.settings, commit=ctx.commit, stages=stages)
    ctx.repo, ctx.commit = target.repo, target.commit
    ctx.fingerprint, ctx.fingerprint_parts = target.fingerprint, target.fingerprint_parts
    ref = begin_snapshot(target.repo, target.commit, target.fingerprint, engine=ctx.engine)
    ctx.snapshot_id = ref.id
    ctx.reused = ref.status == "ready"


# --- the work directory -------------------------------------------------------------------


def build_dir(settings: Settings, full_name: str, commit: str, config: str) -> Path:
    """Where a build of (repo, commit, fingerprint) keeps its work between attempts. The name
    is a digest of that identity, so it never depends on a database's row IDs."""
    identity = digest({"repo": full_name.lower(), "commit": commit, "fingerprint": config})
    return settings.data_dir / "builds" / identity[:24]


def open_build_dir(ctx: BuildContext) -> Path:
    """Create the build's work directory (with a ``build.json`` saying whose it is)."""
    repo = ctx.need(ctx.repo, "repository")
    commit, config = ctx.need(ctx.commit, "commit"), ctx.need(ctx.fingerprint, "fingerprint")
    path = build_dir(ctx.settings, repo.ref.full_name, commit, config)
    path.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, JsonValue] = {
        "repo": repo.ref.full_name,
        "commit": commit,
        "fingerprint": config,
        "snapshot_id": ctx.snapshot_id,
    }
    write_text_lf(path / "build.json", pretty_json(manifest))
    return path


def _checkpoint(ctx: BuildContext, name: str) -> Path | None:
    return None if ctx.workdir is None else ctx.workdir / f"{name}.json"


def _load[M: BaseModel](ctx: BuildContext, name: str, model: type[M]) -> M | None:
    """A stage's output from an earlier attempt, if it was saved and still reads back."""
    path = _checkpoint(ctx, name)
    if path is None or not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:  # pydantic's ValidationError is a ValueError
        log.warning("discarding an unreadable checkpoint", path=str(path), error=str(error))
        path.unlink(missing_ok=True)
        return None


def _save(ctx: BuildContext, name: str, value: BaseModel) -> None:
    """Save a stage's output; written whole and then renamed, so a crash never leaves half."""
    path = _checkpoint(ctx, name)
    if path is None:
        return
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(value.model_dump_json().encode("utf-8"))
    os.replace(partial, path)


# --- the built-in stages ------------------------------------------------------------------


def fetch(ctx: BuildContext) -> None:
    """Fetch the commit's files (depth 1) and its history without file contents; no working
    tree is ever written. A complete fetch from an earlier attempt is reused."""
    repo, commit = ctx.need(ctx.repo, "repository"), ctx.need(ctx.commit, "commit")
    settings = ctx.settings
    label = f"github.com/{repo.ref.full_name}"
    if ctx.workdir is None:
        base = ctx.resources.enter_context(
            Workspace(settings.data_dir / "work", keep=ctx.keep_workdir)
        ).path
    else:
        base = ctx.workdir
        if _resume_fetch(ctx, base, commit, label):
            return
        for leftover in (SOURCE_DIR, HISTORY_DIR):
            remove_tree(base / leftover)
    history = fetch_history(
        repo.ref.clone_url,
        commit,
        base / HISTORY_DIR,
        depth=settings.history_depth,
        timeout=settings.git_timeout_s,
    )
    ctx.history = read_commit_log(history, settings.owner_commits)
    ctx.progress(0.5)
    ctx.tree = ctx.resources.enter_context(
        fetch_source(
            repo.ref.clone_url,
            commit,
            base / SOURCE_DIR,
            label=label,
            timeout=settings.git_timeout_s,
        )
    )
    if ctx.workdir is not None:
        remove_tree(base / HISTORY_DIR)  # the commit log is all the build needs from it
        _save(ctx, "fetch", FetchCheckpoint(commit=commit, history=ctx.history))


def _resume_fetch(ctx: BuildContext, base: Path, commit: str, label: str) -> bool:
    saved = _load(ctx, "fetch", FetchCheckpoint)
    source = base / SOURCE_DIR
    if saved is None or saved.commit != commit or not source.is_dir():
        return False
    try:
        run_git(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=source, timeout=60)
    except FetchError:
        return False
    ctx.history = saved.history
    ctx.tree = ctx.resources.enter_context(
        GitSourceTree(source, commit, label=label, timeout=ctx.settings.git_timeout_s)
    )
    ctx.resumed.append("fetch")
    return True


def inventory(ctx: BuildContext) -> None:
    saved = _load(ctx, "inventory", Inventory)
    if saved is not None:
        ctx.inventory = saved
        ctx.resumed.append("inventory")
        return
    ctx.inventory = build_inventory(
        ctx.need(ctx.tree, "source tree"),
        max_source_files=ctx.settings.max_source_files,
        max_file_bytes=ctx.settings.max_file_bytes,
    )
    _save(ctx, "inventory", ctx.inventory)


def parse(ctx: BuildContext) -> None:
    saved = _load(ctx, "parse", ParseCheckpoint)
    if saved is not None:
        ctx.parsed, ctx.project = saved.files, saved.project
        ctx.resumed.append("parse")
        return
    tree = ctx.need(ctx.tree, "source tree")
    ctx.parsed = parse_files(
        tree,
        ctx.need(ctx.inventory, "inventory"),
        progress=lambda done, total: ctx.progress(done / total),
    )
    ctx.project = read_project_files(tree, max_file_bytes=ctx.settings.max_file_bytes)
    _save(ctx, "parse", ParseCheckpoint(files=ctx.parsed, project=ctx.project))


def graph(ctx: BuildContext) -> None:
    saved = _load(ctx, "graph", CodeGraph)
    if saved is not None:
        ctx.graph = saved
        ctx.resumed.append("graph")
        return
    ctx.graph = build_graph(
        ctx.need(ctx.inventory, "inventory"),
        ctx.need(ctx.parsed, "parse results"),
        ctx.history,
        GraphSettings.from_settings(ctx.settings),
        project=ctx.project,
    )
    _save(ctx, "graph", ctx.graph)


def store(ctx: BuildContext) -> None:
    """Save everything in one transaction (a snapshot that is already ready is left alone)."""
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
        "resumed": list(ctx.resumed),
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
        ready=False,
    )
    if saved != ctx.snapshot_id:
        raise RuntimeError(f"Saved snapshot {saved}, but the build created {ctx.snapshot_id}.")


def publish(ctx: BuildContext) -> None:
    """Make the snapshot ready, adding what later stages measured to its stats."""
    extra: dict[str, JsonValue] = {
        **ctx.stats,
        "timings": dict(ctx.timings),
        "resumed": list(ctx.resumed),
    }
    mark_ready(ctx.need(ctx.snapshot_id, "snapshot"), engine=ctx.engine, extra_stats=extra)


def _parse_components() -> dict[str, JsonValue]:
    return {"extractors": EXTRACTOR_VERSION, **grammar_versions()}


def _graph_components() -> dict[str, JsonValue]:
    return dict(component_versions())


BUILTIN_STAGES: tuple[Stage, ...] = (
    Stage("fetch", 1, ("history_depth", "owner_commits"), fetch, weight=3.0),
    Stage("inventory", 1, ("max_source_files", "max_file_bytes"), inventory, weight=0.5),
    Stage("parse", 1, ("max_file_bytes",), parse, weight=3.0, components=_parse_components),
    Stage("graph", 1, ("owner_commits",), graph, weight=1.0, components=_graph_components),
    Stage("store", 2, (), store, weight=1.5),  # 2: saves as building; publish makes it ready
    Stage("publish", 1, (), publish, weight=0.1),
)
for _stage in BUILTIN_STAGES:
    register_builtin(_stage)


# --- in-process builds --------------------------------------------------------------------


def run_build(
    url: str,
    settings: Settings,
    *,
    engine: Engine | None = None,
    commit: str | None = None,
    keep_workdir: bool = False,
    stages: Sequence[Stage] | None = None,
) -> BuildResult:
    """Resolve, then run ``stages`` (default: the registered ones) in this process. A snapshot
    that is already ready is returned without fetching; a failure marks the snapshot failed."""
    chosen = tuple(registered_stages() if stages is None else stages)
    owned = engine is None
    ctx = BuildContext(
        url=url,
        settings=settings,
        engine=engine or create_db_engine(settings.database_url),
        commit=commit,
        keep_workdir=keep_workdir,
    )
    try:
        started = time.perf_counter()
        resolve(ctx, chosen)
        ctx.timings["resolve"] = round(time.perf_counter() - started, 3)
        if not ctx.reused:
            ctx.workdir = open_build_dir(ctx)
            try:
                with ctx.resources:
                    run_stages(ctx, chosen)
            finally:
                if not keep_workdir:
                    remove_tree(ctx.workdir)
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
