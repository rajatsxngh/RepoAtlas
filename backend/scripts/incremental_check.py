"""Check incremental linking on real commits of real repositories (M16, decision 0024).

For each pinned pair of commits it fetches both, parses them, links the older one, links the
newer one on top of it, and links the newer one from nothing. The two graphs of the newer
commit must have the same digest. It prints what changed, which files' calls were resolved
again and why, and how long each link took.

Usage (from backend/):
    uv run python scripts/incremental_check.py
    uv run python scripts/incremental_check.py https://github.com/<o>/<r> <old sha> <new sha>
    (the old commit may be written ~N: the N-th ancestor of the new one)

Not part of CI: it needs network access (git fetches from GitHub; no API calls, no model, no
database). Exits non-zero if any pair differs.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from repoatlas.config import Settings, get_settings
from repoatlas.graph.build import LinkResult, link_graph
from repoatlas.graph.incremental import previous_from_graph
from repoatlas.graph.project import ProjectFiles
from repoatlas.ingest.fetch import Workspace, fetch_history, fetch_source
from repoatlas.ingest.github import parse_repo_url
from repoatlas.ingest.inventory import Inventory, build_inventory
from repoatlas.parsing.models import ParsedFile
from repoatlas.pipeline.changes import diff_states, inventory_states
from repoatlas.pipeline.graph import read_project_files
from repoatlas.pipeline.parse import parse_files

# (URL, older commit, newer commit). An older commit written "~N" is the N-th first-parent
# ancestor of the newer one, found by fetching that much history.
PINNED = [
    (  # the two zod commits the benchmark work used (M01 and M09)
        "https://github.com/colinhacks/zod",
        "19c40d2072288b4cafec851e9d056a72a88e5973",
        "c7ec94d309ee8acb25113e3db738b3e03816a424",
    ),
    ("https://github.com/colinhacks/zod", "~5", "c7ec94d309ee8acb25113e3db738b3e03816a424"),
    ("https://github.com/pallets/flask", "~40", "49943b5a94413a530761350f04e896f76e8d620e"),
    ("https://github.com/pallets/click", "~40", "cf19179e8e2fd54c601378cec9e0d21bb12b6fb1"),
    ("https://github.com/psf/requests", "~40", "6e83187b8feb273ed4c6cdab5efd8d54901dfab3"),
    ("https://github.com/encode/httpx", "~40", "b5addb64f0161ff6bfe94c124ef76f6a1fba5254"),
    ("https://github.com/remeda/remeda", "~40", "ec4d3f5dad1105c522c83ec4877e04937cb2405e"),
]


@dataclass(frozen=True)
class Commit:
    inventory: Inventory
    files: list[ParsedFile]
    project: ProjectFiles
    parse_s: float


def load(url: str, sha: str, settings: Settings) -> Commit:
    ref = parse_repo_url(url)
    with (
        Workspace(settings.data_dir / "work") as workspace,
        fetch_source(
            ref.clone_url,
            sha,
            workspace.path / "source.git",
            label=f"github.com/{ref.full_name}",
            timeout=settings.git_timeout_s,
        ) as tree,
    ):
        inventory = build_inventory(
            tree,
            max_source_files=settings.max_source_files,
            max_file_bytes=settings.max_file_bytes,
        )
        started = time.perf_counter()
        files = parse_files(tree, inventory)
        parse_s = time.perf_counter() - started
        project = read_project_files(tree, max_file_bytes=settings.max_file_bytes)
    return Commit(inventory, files, project, parse_s)


def ancestor(url: str, sha: str, back: int, settings: Settings) -> str:
    """The ``back``-th first-parent ancestor of ``sha``."""
    ref = parse_repo_url(url)
    with Workspace(settings.data_dir / "work") as workspace:
        history = fetch_history(
            ref.clone_url,
            sha,
            workspace.path / "history.git",
            depth=back + 1,
            timeout=settings.git_timeout_s,
        )
        found = history.git("rev-list", "--first-parent", "-n", "1", f"--skip={back}", sha)
    if not found.strip():
        raise SystemExit(f"{url} has fewer than {back} commits behind {sha[:12]}.")
    return found.strip()


def timed(commit: Commit, **kwargs: object) -> tuple[LinkResult, float]:
    started = time.perf_counter()
    result = link_graph(commit.inventory, commit.files, project=commit.project, **kwargs)  # type: ignore[arg-type]
    return result, time.perf_counter() - started


def check(url: str, old_sha: str, new_sha: str, settings: Settings) -> bool:
    if old_sha.startswith("~"):
        old_sha = ancestor(url, new_sha, int(old_sha[1:]), settings)
    old, new = load(url, old_sha, settings), load(url, new_sha, settings)
    before, _ = timed(old)
    changes = diff_states(
        inventory_states(old.inventory.files), inventory_states(new.inventory.files)
    )
    previous = previous_from_graph(
        before.graph,
        before.records,
        arrived=changes.arrived,
        gone=changes.gone,
        modified=changes.modified,
    )
    incremental, incremental_s = timed(new, previous=previous)
    full, full_s = timed(new)
    same = incremental.graph.digest() == full.graph.digest()
    same = same and incremental.records == full.records
    stats = full.graph.stats
    print(f"{url} {old_sha[:12]} -> {new_sha[:12]}")
    print(f"  files parsed: {len(old.files):,} -> {len(new.files):,}; changes: {changes.counts()}")
    print(
        f"  new graph: {len(full.graph.nodes):,} nodes, {len(full.graph.edges):,} edges, "
        f"{stats.call_sites:,} call sites"
    )
    print(f"  calls resolved again: {incremental.stats()}")
    print(
        f"  parse {new.parse_s:.2f} s; full link {full_s:.2f} s; "
        f"incremental link {incremental_s:.2f} s"
    )
    print(f"  digest {full.graph.digest()}")
    print(f"  incremental equals full: {'yes' if same else 'NO'}")

    # Linking the same commit again must reuse every file.
    again_changes = diff_states(
        inventory_states(new.inventory.files), inventory_states(new.inventory.files)
    )
    again_previous = previous_from_graph(
        full.graph, full.records, arrived=again_changes.arrived, gone=[], modified=[]
    )
    again, again_s = timed(new, previous=again_previous)
    unchanged = again.graph.digest() == full.graph.digest()
    print(
        f"  nothing changed: {again.stats()} in {again_s:.2f} s; "
        f"equal: {'yes' if unchanged else 'NO'}"
    )
    return same and unchanged


def main(argv: list[str]) -> int:
    settings = get_settings()
    pairs = [(argv[0], argv[1], argv[2])] if len(argv) == 3 else PINNED
    if argv and len(argv) != 3:
        print(__doc__)
        return 2
    ok = True
    for url, old_sha, new_sha in pairs:
        ok &= check(url, old_sha, new_sha, settings)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
