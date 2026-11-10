"""What the stages of a build share (``BuildContext``)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.graph.model import CodeGraph
from repoatlas.graph.owners import CommitLog
from repoatlas.graph.project import ProjectFiles
from repoatlas.ingest.github import RepoMeta
from repoatlas.ingest.inventory import Inventory
from repoatlas.ingest.source import SourceTree
from repoatlas.parsing.models import ParsedFile
from repoatlas.store.models import SnapshotId


@dataclass
class BuildContext:
    """The state of one build. Each stage reads earlier stages' fields and fills its own.

        ``workdir`` is where stages keep their output between attempts (checkpoints): a retried
        build finds it there and resumes instead of starting over. Without one, stages work in a
        temporary directory and keep nothing. ``resumed`` lists the stages whose output came from
        a checkpoint. A stage reports how far it has got with ``progress``; stages after ``store``
    put what they measured in ``stats``, which ``publish`` adds to the snapshot's stats.
    """

    url: str
    settings: Settings
    engine: Engine
    commit: str | None = None  # pinned by resolve when not requested
    keep_workdir: bool = False
    repo: RepoMeta | None = None
    fingerprint: str | None = None
    fingerprint_parts: dict[str, JsonValue] = field(default_factory=dict)
    snapshot_id: SnapshotId | None = None
    reused: bool = False  # a ready snapshot already existed; nothing is built
    history: CommitLog | None = None
    tree: SourceTree | None = None
    inventory: Inventory | None = None
    parsed: list[ParsedFile] | None = None
    project: ProjectFiles | None = None
    graph: CodeGraph | None = None
    timings: dict[str, float] = field(default_factory=dict)
    stats: dict[str, JsonValue] = field(default_factory=dict)  # merged into the snapshot's stats
    resources: ExitStack = field(default_factory=ExitStack)
    workdir: Path | None = None
    resumed: list[str] = field(default_factory=list)
    on_progress: Callable[[float], None] | None = None  # set by the stage runner

    def need[T](self, value: T | None, what: str) -> T:
        if value is None:
            raise RuntimeError(f"The build has no {what} yet; a step ran out of order.")
        return value

    def progress(self, fraction: float) -> None:
        """Report the share (0 to 1) of the current stage that is done."""
        if self.on_progress is not None:
            self.on_progress(min(max(fraction, 0.0), 1.0))
