"""Golden tests: the fixture graphs must be byte-identical on every OS.

Regenerate after an intentional change with:
    REPOATLAS_UPDATE_GOLDEN=1 uv run pytest tests/unit/test_graph_golden.py
then review the diff of tests/fixtures/golden/*.graph.json before committing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.graph.build import build_graph
from repoatlas.graph.model import CodeGraph, EdgeKind, NodeKind
from repoatlas.ingest.source import DirectorySourceTree
from repoatlas.parsing.models import Range
from repoatlas.pipeline.graph import GraphOutput, graph_tree, read_project_files
from repoatlas.pipeline.parse import parse_tree

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MAX_FILES, MAX_BYTES = 100, 100_000
CAPS = {"max_source_files": MAX_FILES, "max_file_bytes": MAX_BYTES}
NAMES = ["py_small", "ts_small"]


def build(name: str) -> GraphOutput:
    with DirectorySourceTree(FIXTURES / "repos" / name, label=f"dir:{name}") as tree:
        return graph_tree(tree, max_source_files=MAX_FILES, max_file_bytes=MAX_BYTES)


@pytest.mark.parametrize("name", NAMES)
def test_fixture_graph_matches_golden(name: str) -> None:
    output = build(name)
    actual = pretty_json(output)
    golden = FIXTURES / "golden" / f"{name}.graph.json"
    if os.environ.get("REPOATLAS_UPDATE_GOLDEN") == "1":
        write_text_lf(golden, actual)
    expected = golden.read_bytes().decode("utf-8")
    assert expected == actual, (
        f"{golden.name} differs; if the change is intended, regenerate it (see module docstring)"
    )
    data = json.loads(expected)
    assert CodeGraph.model_validate(data["graph"]).digest() == data["digest"]


@pytest.mark.parametrize("name", NAMES)
def test_builds_are_deterministic(name: str) -> None:
    root = FIXTURES / "repos" / name
    with DirectorySourceTree(root, label=f"dir:{name}") as tree:
        parsed = parse_tree(tree, **CAPS)
        project = read_project_files(tree, max_file_bytes=CAPS["max_file_bytes"])
    first = build_graph(parsed.inventory, parsed.files, project=project)
    again = build_graph(parsed.inventory, parsed.files, project=project)
    shuffled = build_graph(parsed.inventory, list(reversed(parsed.files)), project=project)
    assert first.digest() == again.digest() == shuffled.digest() == build(name).digest
    assert CodeGraph.from_json(first.to_json()).digest() == first.digest()


def _text(root: Path, path: str, site: Range) -> str:
    lines = (root / path).read_bytes().split(b"\n")
    if site.start_line == site.end_line:
        return lines[site.start_line - 1][site.start_col : site.end_col].decode("utf-8")
    first = lines[site.start_line - 1][site.start_col :]
    middle = lines[site.start_line : site.end_line - 1]
    last = lines[site.end_line - 1][: site.end_col]
    return b"\n".join([first, *middle, last]).decode("utf-8")


def _within(inner: Range, outer: Range) -> bool:
    start = (outer.start_line, outer.start_col) <= (inner.start_line, inner.start_col)
    return start and (inner.end_line, inner.end_col) <= (outer.end_line, outer.end_col)


@pytest.mark.parametrize("name", NAMES)
def test_edge_sites_point_at_their_evidence(name: str) -> None:
    root = FIXTURES / "repos" / name
    graph = build(name).graph
    for edge in graph.edges:
        src, dst = graph.nodes[edge.src], graph.nodes[edge.dst]
        if edge.kind == EdgeKind.CONTAINS:
            assert edge.site is None and dst.parent_key == src.key
            if src.span is not None and dst.span is not None:
                assert _within(dst.span, src.span), (src.key, dst.key)
            continue
        if edge.kind == EdgeKind.TESTED_BY:
            assert dst.meta.get("is_test") is True or dst.kind == NodeKind.TEST
            if edge.site is not None:  # the evidence (an import or a call) is in the test file
                assert _text(root, dst.path, edge.site)
            continue
        assert edge.site is not None or edge.kind == EdgeKind.ROUTES_TO, edge
        if edge.site is None:
            continue
        text = _text(root, src.path, edge.site)
        if edge.kind == EdgeKind.IMPORTS:
            assert any(word in text for word in ("import", "require", "export")), text
        elif edge.kind == EdgeKind.CALLS:
            assert "(" in text or text.startswith("<"), text
            if src.span is not None:
                assert _within(edge.site, src.span), (edge.src, text)
        elif edge.kind == EdgeKind.INHERITS:
            assert text.split("[")[0].split("<")[0].endswith(dst.name), (text, dst.key)
        elif edge.kind == EdgeKind.ROUTES_TO:
            assert src.kind == NodeKind.ROUTE


def test_fixture_graphs_cover_every_edge_kind() -> None:
    kinds = set()
    for name in NAMES:
        kinds |= set(build(name).graph.stats.edges_by_kind)
    assert kinds == {kind.value for kind in EdgeKind}
