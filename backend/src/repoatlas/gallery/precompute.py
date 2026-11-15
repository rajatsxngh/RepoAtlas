"""Precompute the gallery (LCH-2): build every repository of the list at its pinned commit and
record it in the ``gallery`` table.

Each entry goes through the ordinary build (``run_build``: fetch, inventory, parse, graph,
store, chunk, embed, publish), which calls no model. A snapshot that is already ready is
reused, so running the command again costs nothing and repairs what failed last time. An
entry that can't be fetched or stored does not stop the others; its row (if an earlier run
made one) is left alone. When the whole list was attempted, rows of repositories that left
the list are removed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.gallery.listing import GalleryEntry, GalleryList
from repoatlas.gallery.store import prune_gallery, save_gallery_entry
from repoatlas.ingest.errors import IngestError
from repoatlas.pipeline.build import BuildResult, run_build
from repoatlas.store.errors import StoreError

__all__ = ["PrecomputeResult", "precompute"]


class Build(Protocol):
    def __call__(
        self, url: str, settings: Settings, *, engine: Engine, commit: str
    ) -> BuildResult: ...


@dataclass(frozen=True)
class PrecomputeResult:
    """What happened to one entry: its snapshot, or why it has none."""

    repo: str
    commit: str
    snapshot_id: int | None
    reused: bool
    seconds: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def precompute(
    listing: GalleryList,
    settings: Settings,
    *,
    engine: Engine,
    only: Collection[str] | None = None,
    build: Build = run_build,
    report: Callable[[PrecomputeResult], None] | None = None,
) -> list[PrecomputeResult]:
    """Build the entries of ``listing`` (those named in ``only``, as ``owner/name`` in any
    case, when given) and record each built one at its position in the list. ``report`` is
    called after each entry. With ``only``, no row is removed."""
    wanted = None if only is None else {name.lower() for name in only}
    results: list[PrecomputeResult] = []
    for position, entry in enumerate(listing.repos):
        if wanted is not None and entry.ref.key not in wanted:
            continue
        result = _one(entry, position, settings, engine, build)
        results.append(result)
        if report is not None:
            report(result)
    if wanted is None:
        prune_gallery({entry.ref.key for entry in listing.repos}, engine=engine)
    return results


def _one(
    entry: GalleryEntry, position: int, settings: Settings, engine: Engine, build: Build
) -> PrecomputeResult:
    started = time.perf_counter()
    snapshot_id: int | None = None
    reused = False
    error: str | None = None
    try:
        built = build(entry.url, settings, engine=engine, commit=entry.commit)
        snapshot_id, reused = int(built.snapshot_id), built.reused
        save_gallery_entry(entry, snapshot_id, position, engine=engine)
    except (IngestError, StoreError) as failure:
        error = str(failure)
    return PrecomputeResult(
        repo=entry.ref.full_name,
        commit=entry.commit,
        snapshot_id=snapshot_id,
        reused=reused,
        seconds=round(time.perf_counter() - started, 1),
        error=error,
    )
