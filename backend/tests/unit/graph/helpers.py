"""Helpers for graph tests: build a graph from files held in memory."""

from __future__ import annotations

from collections.abc import Mapping

from repoatlas.graph.build import build_graph
from repoatlas.graph.model import CodeGraph, Edge, EdgeKind
from repoatlas.graph.project import ProjectFiles
from repoatlas.ingest.source import MemorySourceTree
from repoatlas.pipeline.parse import ParseOutput, parse_tree

CAPS = {"max_source_files": 1_000, "max_file_bytes": 1_000_000}


def parse_files(files: Mapping[str, str | bytes]) -> ParseOutput:
    data: dict[str, bytes | tuple[str, bytes]] = {
        path: text.encode("utf-8") if isinstance(text, str) else text
        for path, text in files.items()
    }
    with MemorySourceTree(data) as tree:
        return parse_tree(tree, **CAPS)


def graph_of(
    files: Mapping[str, str | bytes], config: Mapping[str, str] | None = None
) -> CodeGraph:
    """Parse ``files`` and build their graph; ``config`` holds project files (tsconfig.json…)."""
    output = parse_files(files)
    project = ProjectFiles(files=dict(config or {}))
    return build_graph(output.inventory, output.files, project=project)


def pairs(graph: CodeGraph, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(edge.src, edge.dst) for edge in graph.edges if edge.kind == kind}


def edge(graph: CodeGraph, kind: EdgeKind, src: str, dst: str) -> Edge:
    matches = [e for e in graph.edges if e.kind == kind and e.src == src and e.dst == dst]
    assert len(matches) == 1, (kind, src, dst, matches)
    return matches[0]
