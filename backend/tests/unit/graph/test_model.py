from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from repoatlas.graph.model import (
    CodeGraph,
    Edge,
    EdgeKind,
    GraphStats,
    Node,
    NodeKind,
    Owner,
    Provenance,
    UnresolvedCall,
)
from repoatlas.parsing.models import Range


def rng(line: int, start: int = 0, end: int = 5) -> Range:
    return Range(start_line=line, start_col=start, end_line=line, end_col=end)


def sample_graph(shuffle_seed: int | None = None) -> CodeGraph:
    nodes = [
        Node(key="dir:.", kind=NodeKind.DIR, name=".", qualified_name=".", path="."),
        Node(
            key="file:app.py",
            kind=NodeKind.FILE,
            name="app.py",
            qualified_name="app.py",
            path="app.py",
            parent_key="dir:.",
            meta={"language": "py", "is_test": False},
        ),
        Node(
            key="py:app.py:main:function",
            kind=NodeKind.FUNCTION,
            name="main",
            qualified_name="main",
            path="app.py",
            name_range=rng(1, 4, 8),
            span=Range(start_line=1, start_col=0, end_line=2, end_col=13),
            parent_key="file:app.py",
            exported=True,
            meta={"signature": "def main()"},
        ),
        Node(
            key="py:app.py:run:function",
            kind=NodeKind.FUNCTION,
            name="run",
            qualified_name="run",
            path="app.py",
            name_range=rng(4, 4, 7),
            span=Range(start_line=4, start_col=0, end_line=5, end_col=8),
            parent_key="file:app.py",
            exported=True,
        ),
    ]
    edges = [
        Edge(
            src="dir:.",
            dst="file:app.py",
            kind=EdgeKind.CONTAINS,
            provenance=Provenance.TREE_SITTER,
            confidence=1.0,
        ),
        Edge(
            src="py:app.py:main:function",
            dst="py:app.py:run:function",
            kind=EdgeKind.CALLS,
            provenance=Provenance.NAME_MATCH,
            confidence=0.95,
            site=rng(2, 11, 16),
            meta={"tier": "T1", "count": 1},
        ),
        Edge(
            src="py:app.py:main:function",
            dst="py:app.py:run:function",
            kind=EdgeKind.CALLS,
            provenance=Provenance.NAME_MATCH,
            confidence=0.3 / 3,
            site=rng(3, 4, 9),
            meta={"tier": "T6", "count": 1, "candidates": 3},
        ),
    ]
    unresolved = [
        UnresolvedCall(
            caller_key="py:app.py:main:function",
            callee_text="obj.get",
            receiver_text="obj",
            site=rng(2, 4, 13),
            reason="not_found",
        ),
        UnresolvedCall(
            caller_key="file:app.py",
            callee_text="handlers[k]",
            receiver_text=None,
            site=rng(7),
            reason="dynamic",
        ),
    ]
    owners = [
        Owner(
            author_name="Ada",
            author_hash="a1b2c3d4e5f60718",
            commits=1,
            last_commit_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            share=1 / 3,
        ),
        Owner(
            author_name="Grace",
            author_hash="0f1e2d3c4b5a6978",
            commits=2,
            last_commit_at=datetime(2025, 6, 1, tzinfo=UTC),
            share=2 / 3,
        ),
    ]
    if shuffle_seed is not None:
        shuffler = random.Random(shuffle_seed)
        for items in (nodes, edges, unresolved, owners):
            shuffler.shuffle(items)
    return CodeGraph(
        nodes={node.key: node for node in nodes},
        edges=edges,
        unresolved=unresolved,
        owners={"app.py": owners},
        stats=GraphStats(nodes_by_kind={"dir": 1, "file": 1, "function": 2}, call_sites=3),
    )


def test_vocabulary_matches_the_prd() -> None:
    assert [k.value for k in NodeKind] == [
        "dir",
        "file",
        "class",
        "function",
        "method",
        "route",
        "test",
    ]
    assert [k.value for k in EdgeKind] == [
        "contains",
        "imports",
        "calls",
        "inherits",
        "tested_by",
        "routes_to",
    ]
    assert [p.value for p in Provenance] == [
        "tree_sitter",
        "import_resolver",
        "name_match",
        "convention",
        "git",
    ]


def test_json_round_trip_keeps_content_and_digest() -> None:
    graph = sample_graph()
    restored = CodeGraph.from_json(graph.to_json())
    assert restored == graph
    assert restored.digest() == graph.digest()
    assert restored.to_json() == graph.to_json()
    assert CodeGraph.from_json(graph.to_json().encode("utf-8")) == graph


def test_collections_are_canonical_whatever_the_input_order() -> None:
    expected = sample_graph()
    for seed in range(5):
        shuffled = sample_graph(shuffle_seed=seed)
        assert shuffled.to_json() == expected.to_json()
        assert shuffled.digest() == expected.digest()
    assert list(expected.nodes) == sorted(expected.nodes)
    sites = [e.site.start_line for e in expected.edges if e.site is not None]
    assert sites == sorted(sites)
    assert [o.author_name for o in expected.owners["app.py"]] == ["Grace", "Ada"]


def test_to_json_is_compact_with_sorted_keys() -> None:
    text = sample_graph().to_json()
    assert text.startswith('{"edges":[')
    assert ": " not in text and "\n" not in text
    data = json.loads(text)
    assert list(data) == ["edges", "nodes", "owners", "stats", "unresolved"]


def test_digest_changes_with_content() -> None:
    graph = sample_graph()
    changed = graph.model_copy(update={"edges": graph.edges[:-1]})
    assert CodeGraph.model_validate(changed.model_dump()).digest() != graph.digest()


def test_node_keys_must_match_their_dict_keys() -> None:
    node = Node(
        key="file:a.py", kind=NodeKind.FILE, name="a.py", qualified_name="a.py", path="a.py"
    )
    with pytest.raises(ValidationError, match="is stored under"):
        CodeGraph(nodes={"file:b.py": node})


@pytest.mark.parametrize("confidence", [0, -0.1, 1.01, 0.00001])
def test_confidence_must_be_in_the_unit_interval(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Edge(
            src="a",
            dst="b",
            kind=EdgeKind.CALLS,
            provenance=Provenance.NAME_MATCH,
            confidence=confidence,
        )


def test_confidences_and_shares_are_rounded() -> None:
    edge = Edge(
        src="a", dst="b", kind=EdgeKind.CALLS, provenance=Provenance.NAME_MATCH, confidence=0.3 / 3
    )
    assert edge.confidence == 0.1
    assert '"confidence":0.1' in CodeGraph(edges=[edge]).to_json()
    owner = Owner(
        author_name="A",
        author_hash="h",
        commits=1,
        last_commit_at=datetime(2026, 1, 1, tzinfo=UTC),
        share=2 / 3,
    )
    assert owner.share == 0.6667


def test_owner_times_are_utc_and_must_be_aware() -> None:
    eastern = timezone(timedelta(hours=-5))
    owner = Owner(
        author_name="A",
        author_hash="h",
        commits=1,
        last_commit_at=datetime(2026, 1, 1, 7, tzinfo=eastern),
        share=1.0,
    )
    assert owner.last_commit_at == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert '"last_commit_at":"2026-01-01T12:00:00Z"' in owner.model_dump_json()
    with pytest.raises(ValidationError, match="timezone-aware"):
        Owner(
            author_name="A",
            author_hash="h",
            commits=1,
            last_commit_at=datetime(2026, 1, 1),
            share=1.0,
        )


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    node = Node(key="dir:.", kind=NodeKind.DIR, name=".", qualified_name=".", path=".")
    with pytest.raises(ValidationError):
        node.name = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Node.model_validate(
            {"key": "dir:.", "kind": "dir", "name": ".", "qualified_name": ".", "path": ".", "x": 1}
        )


def test_edges_of_filters_by_kind() -> None:
    graph = sample_graph()
    assert len(graph.edges_of(EdgeKind.CALLS)) == 2
    assert graph.edges_of(EdgeKind.IMPORTS) == []
