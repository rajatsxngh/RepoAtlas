"""Build a snapshot of a directory on this machine, through the real pipeline.

For fixture repositories that ship with RepoAtlas (the planted-injection fixture of the
``injection`` suite): no GitHub, no network. The directory's files become one commit in a
throwaway bare git repository; from there the build is the ordinary one (the registered stages:
inventory, parse, graph, store, chunks, embeddings, publish), with only the fetch pointed at
that local repository. Code reads still go through the store's blobs, like any snapshot.

The commit is deterministic (fixed author and date), so the same files give the same commit
SHA, and a snapshot that is already ready for it and for the current pipeline configuration is
reused. Files are read as bytes and never executed.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.graph.owners import read_commit_log
from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.fetch import fetch_history, fetch_source, remove_tree
from repoatlas.ingest.git import git_command, git_env, run_git
from repoatlas.ingest.github import RepoMeta, RepoRef
from repoatlas.pipeline import fingerprint
from repoatlas.pipeline.build import HISTORY_DIR, SOURCE_DIR, BuildResult, open_build_dir
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import Stage, fingerprint_parts, registered_stages, run_stages
from repoatlas.store.loader import begin_snapshot, mark_failed

FIXTURE_OWNER = "repoatlas-fixtures"
_WHEN = "2026-01-01T00:00:00+00:00"
_IDENTITY = {
    "GIT_AUTHOR_NAME": "RepoAtlas Fixtures",
    "GIT_AUTHOR_EMAIL": "fixtures@repoatlas.invalid",
    "GIT_AUTHOR_DATE": _WHEN,
    "GIT_COMMITTER_NAME": "RepoAtlas Fixtures",
    "GIT_COMMITTER_EMAIL": "fixtures@repoatlas.invalid",
    "GIT_COMMITTER_DATE": _WHEN,
}
SKIP_DIRS = frozenset({"__pycache__", ".git", "node_modules", ".pytest_cache"})


def directory_files(directory: Path) -> list[tuple[str, bytes]]:
    """Every file under ``directory`` as (posix path, bytes), sorted by path."""
    found = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(directory).parts):
            found.append((path.relative_to(directory).as_posix(), path.read_bytes()))
    if not found:
        raise FetchError(f"{directory} has no files.")
    return found


def _tree(repo: Path, files: list[tuple[str, bytes]], prefix: str = "") -> str:
    lines: list[str] = []
    children: dict[str, list[tuple[str, bytes]]] = {}
    for path, data in files:
        head, sep, _rest = path[len(prefix) :].partition("/")
        if sep:
            children.setdefault(head, []).append((path, data))
        else:
            oid = run_git(["hash-object", "-w", "--stdin"], cwd=repo, input=data).decode().strip()
            lines.append(f"100644 blob {oid}\t{head}")
    for name, nested in children.items():
        lines.append(f"040000 tree {_tree(repo, nested, prefix + name + '/')}\t{name}")
    listing = "".join(line + "\0" for line in lines).encode("utf-8")
    return run_git(["mktree", "-z"], cwd=repo, input=listing).decode().strip()


def commit_directory(directory: Path, repo: Path) -> str:
    """Make ``repo`` a bare repository whose ``main`` is one commit of ``directory``'s files;
    return the commit SHA (the same for the same files)."""
    run_git(["init", "--bare", "-q", str(repo)])
    run_git(["config", "uploadpack.allowAnySHA1InWant", "true"], cwd=repo)
    run_git(["config", "uploadpack.allowFilter", "true"], cwd=repo)
    tree = _tree(repo, directory_files(directory))
    proc = subprocess.run(
        git_command(["commit-tree", tree, "-m", f"Fixture snapshot of {directory.name}"]),
        cwd=repo,
        env={**git_env(), **_IDENTITY},
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise FetchError("git commit-tree failed for the fixture repository.")
    commit = proc.stdout.decode().strip()
    run_git(["update-ref", "refs/heads/main", commit], cwd=repo)
    return commit


def _local_fetch(origin_url: str) -> Stage:
    """The build's fetch stage, reading from ``origin_url`` instead of GitHub."""

    def run(ctx: BuildContext) -> None:
        commit = ctx.need(ctx.commit, "commit")
        repo = ctx.need(ctx.repo, "repository")
        base = ctx.need(ctx.workdir, "work directory")
        for leftover in (SOURCE_DIR, HISTORY_DIR):
            remove_tree(base / leftover)
        timeout = ctx.settings.git_timeout_s
        history = fetch_history(
            origin_url,
            commit,
            base / HISTORY_DIR,
            depth=ctx.settings.history_depth,
            timeout=timeout,
        )
        ctx.history = read_commit_log(history, ctx.settings.owner_commits)
        ctx.tree = ctx.resources.enter_context(
            fetch_source(
                origin_url,
                commit,
                base / SOURCE_DIR,
                label=f"fixture {repo.ref.full_name}",
                timeout=timeout,
            )
        )

    return run  # type: ignore[return-value]


def build_local_snapshot(
    directory: Path,
    settings: Settings,
    engine: Engine,
    *,
    name: str | None = None,
    owner: str = FIXTURE_OWNER,
) -> BuildResult:
    """Build (or reuse) the snapshot of ``directory`` as repository ``owner/name``."""
    name = name or directory.name
    meta = RepoMeta(
        ref=RepoRef(owner, name),
        default_branch="main",
        private=False,
        size_kb=max(1, sum(len(data) for _, data in directory_files(directory)) // 1024),
        html_url="",
        archived=False,
        fork=False,
    )
    origin = Path(tempfile.mkdtemp(prefix="repoatlas-fixture-"))
    ctx: BuildContext | None = None
    try:
        commit = commit_directory(directory, origin / "origin.git")
        fetch = _local_fetch((origin / "origin.git").resolve().as_uri())
        stages = tuple(
            replace(stage, run=fetch) if stage.name == "fetch" else stage  # type: ignore[arg-type]
            for stage in registered_stages()
        )
        parts = fingerprint_parts(settings, stages)
        ctx = BuildContext(
            url=f"fixture://{owner}/{name}",
            settings=settings,
            engine=engine,
            commit=commit,
            repo=meta,
            fingerprint=fingerprint.compute(parts),
            fingerprint_parts=parts,
        )
        started = time.perf_counter()
        ref = begin_snapshot(meta, commit, ctx.need(ctx.fingerprint, "fingerprint"), engine=engine)
        ctx.snapshot_id = ref.id
        ctx.reused = ref.status == "ready"
        ctx.timings["resolve"] = round(time.perf_counter() - started, 3)
        if not ctx.reused:
            ctx.workdir = open_build_dir(ctx)
            try:
                with ctx.resources:
                    run_stages(ctx, stages)
            finally:
                remove_tree(ctx.workdir)
    except Exception as error:
        if ctx is not None and ctx.snapshot_id is not None and not ctx.reused:
            mark_failed(ctx.snapshot_id, error, engine=engine)
        raise
    finally:
        remove_tree(origin)
    return BuildResult(
        snapshot_id=ctx.need(ctx.snapshot_id, "snapshot"),
        reused=ctx.reused,
        repo=meta.ref.full_name,
        commit=commit,
        fingerprint=ctx.need(ctx.fingerprint, "fingerprint"),
        timings=ctx.timings,
    )
