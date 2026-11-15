"""Helpers for incremental-build tests: link a repository held in memory, then link its next
version on top of the first, and compare with linking the next version from nothing."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from repoatlas.graph.build import LinkResult, link_graph
from repoatlas.graph.incremental import ALL_RULES, Rule, previous_from_graph
from repoatlas.graph.project import ProjectFiles
from repoatlas.pipeline.changes import ChangeSet, diff_states, inventory_states
from repoatlas.pipeline.graph import PROJECT_FILE_NAMES
from repoatlas.pipeline.parse import ParseOutput
from tests.unit.graph.helpers import parse_files

Files = Mapping[str, str | bytes]


def project_of(files: Files) -> ProjectFiles:
    """The project files among ``files`` (``pyproject.toml``, ``package.json``, tsconfigs)."""
    found: dict[str, str] = {}
    for path, text in files.items():
        name = path.rsplit("/", 1)[-1]
        if name in PROJECT_FILE_NAMES or name.startswith(("tsconfig", "jsconfig")):
            found[path] = text if isinstance(text, str) else text.decode("utf-8")
    return ProjectFiles(files=dict(sorted(found.items())))


@dataclass(frozen=True)
class Linked:
    files: Files
    output: ParseOutput
    result: LinkResult


def link(files: Files) -> Linked:
    """A full link of ``files``."""
    output = parse_files(files)
    result = link_graph(output.inventory, output.files, project=project_of(files))
    return Linked(files, output, result)


@dataclass(frozen=True)
class Relinked:
    changes: ChangeSet
    incremental: LinkResult
    full: LinkResult

    @property
    def equal(self) -> bool:
        return self.incremental.graph.digest() == self.full.graph.digest()

    @property
    def reasons(self) -> dict[str, str]:
        assert self.incremental.plan is not None
        return self.incremental.plan.resolve

    @property
    def reused(self) -> list[str]:
        assert self.incremental.plan is not None
        return self.incremental.plan.reuse


def relink(before: Linked, files: Files, *, rules: Collection[Rule] = ALL_RULES) -> Relinked:
    """Link ``files`` reusing ``before``, and link them from nothing."""
    output = parse_files(files)
    changes = diff_states(
        inventory_states(before.output.inventory.files), inventory_states(output.inventory.files)
    )
    previous = previous_from_graph(
        before.result.graph,
        before.result.records,
        arrived=changes.arrived,
        gone=changes.gone,
        modified=changes.modified,
    )
    project = project_of(files)
    incremental = link_graph(
        output.inventory, output.files, project=project, previous=previous, rules=rules
    )
    full = link_graph(output.inventory, output.files, project=project)
    return Relinked(changes, incremental, full)


def assert_same(relinked: Relinked) -> None:
    """The incremental graph and records equal the full link's."""
    inc, full = relinked.incremental, relinked.full
    assert inc.graph.digest() == full.graph.digest(), _difference(relinked)
    assert inc.records == full.records


def _difference(relinked: Relinked) -> str:
    inc, full = relinked.incremental.graph, relinked.full.graph
    lines = [f"changes: {relinked.changes.counts()}", f"resolved: {relinked.reasons}"]
    ours, theirs = set(map(repr, inc.edges)), set(map(repr, full.edges))
    lines += [f"extra edge: {edge}" for edge in sorted(ours - theirs)[:5]]
    lines += [f"missing edge: {edge}" for edge in sorted(theirs - ours)[:5]]
    ours, theirs = set(map(repr, inc.unresolved)), set(map(repr, full.unresolved))
    lines += [f"extra unresolved: {call}" for call in sorted(ours - theirs)[:5]]
    lines += [f"missing unresolved: {call}" for call in sorted(theirs - ours)[:5]]
    if inc.nodes != full.nodes:
        lines.append("nodes differ")
    if inc.stats != full.stats:
        lines.append(f"stats differ: {inc.stats} vs {full.stats}")
    return "\n".join(lines)
