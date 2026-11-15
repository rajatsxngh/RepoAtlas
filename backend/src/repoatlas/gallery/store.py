"""The ``gallery`` table: written by ``repoatlas precompute``, read by ``GET /gallery``.

A row is one repository of the list file with the snapshot built for its pinned commit.
``read_gallery`` returns only rows whose snapshot is ready, so a card never opens a build.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text

from repoatlas.gallery.listing import GalleryEntry

__all__ = ["GalleryItem", "prune_gallery", "read_gallery", "save_gallery_entry"]


class GalleryItem(BaseModel):
    """A gallery card: a ready snapshot of a well-known repository, with text from the list
    file and sizes from the build's stats."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: int
    repo: str = Field(description="`owner/name` on GitHub.")
    commit_sha: str = Field(description="The pinned commit the snapshot was built from.")
    title: str
    description: str
    language: str
    license: str = Field(description="SPDX identifier of the repository's license.")
    benchmark: bool = Field(description="One of the repositories the published numbers use.")
    files: int = Field(description="Source files in the map.")
    nodes: int
    edges: int


def save_gallery_entry(
    entry: GalleryEntry, snapshot_id: int, position: int, *, engine: Engine
) -> None:
    """Insert or replace the row of ``entry``'s repository."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gallery (repo_key, position, snapshot_id, title, description, "
                "language, license, benchmark) VALUES (:repo_key, :position, :snapshot_id, "
                ":title, :description, :language, :license, :benchmark) "
                "ON CONFLICT (repo_key) DO UPDATE SET position = EXCLUDED.position, "
                "snapshot_id = EXCLUDED.snapshot_id, title = EXCLUDED.title, "
                "description = EXCLUDED.description, language = EXCLUDED.language, "
                "license = EXCLUDED.license, benchmark = EXCLUDED.benchmark, updated_at = now()"
            ),
            {
                "repo_key": entry.ref.key,
                "position": position,
                "snapshot_id": snapshot_id,
                "title": entry.title,
                "description": entry.description,
                "language": entry.language,
                "license": entry.license,
                "benchmark": entry.benchmark,
            },
        )


def prune_gallery(keep: Collection[str], *, engine: Engine) -> int:
    """Remove the rows of repositories whose key is not in ``keep``; return how many."""
    with engine.begin() as connection:
        removed = connection.execute(
            text("DELETE FROM gallery WHERE NOT (repo_key = ANY(:keep))"), {"keep": sorted(keep)}
        )
    return int(removed.rowcount)


def read_gallery(*, engine: Engine) -> list[GalleryItem]:
    """The gallery's cards in list order: rows whose snapshot is ready."""
    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT g.snapshot_id, r.owner, r.name, s.commit_sha, g.title, g.description, "
                "g.language, g.license, g.benchmark, s.stats FROM gallery g "
                "JOIN snapshots s ON s.id = g.snapshot_id JOIN repos r ON r.id = s.repo_id "
                "WHERE s.status = 'ready' ORDER BY g.position, g.repo_key"
            )
        ).all()
    return [
        GalleryItem(
            snapshot_id=row.snapshot_id,
            repo=f"{row.owner}/{row.name}",
            commit_sha=row.commit_sha,
            title=row.title,
            description=row.description,
            language=row.language,
            license=row.license,
            benchmark=row.benchmark,
            **_sizes(row.stats),
        )
        for row in found
    ]


def _sizes(stats: Any) -> dict[str, int]:
    """File, node, and edge counts from a snapshot's stats (zero where a count is absent)."""
    data = stats if isinstance(stats, dict) else {}
    graph = data.get("graph")
    graph = graph if isinstance(graph, dict) else {}
    inventory = data.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    return {
        "files": _count(inventory.get("indexed")),
        "nodes": _total(graph.get("nodes_by_kind")),
        "edges": _total(graph.get("edges_by_kind")),
    }


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _total(counts: Any) -> int:
    return sum(_count(value) for value in counts.values()) if isinstance(counts, dict) else 0
