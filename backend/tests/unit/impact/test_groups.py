"""Groups and ranking (T2): tests, public APIs, callers; one combined ranking."""

from __future__ import annotations

from functools import cache

import pytest

from repoatlas.graph.model import Node, NodeKind
from repoatlas.impact.data import MemoryImpactData
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.groups import count_by_group, group_of, ranked
from repoatlas.impact.schema import ImpactParams, ImpactReport, SymbolTarget
from repoatlas.store.errors import NodeNotFound
from tests.unit.impact.helpers import (
    BASE_RUN,
    CHILD_RUN,
    MAIN,
    PARSE,
    TEST_PARSE,
    TOKENIZE,
    project_data,
)


@cache
def data() -> MemoryImpactData:
    return project_data()


def analyze(key: str, **params: object) -> ImpactReport:
    return ImpactEngine(data()).analyze(
        1, SymbolTarget(symbol_key=key), ImpactParams(**params) if params else None
    )


def node(kind: NodeKind, **meta: object) -> Node:
    return Node(key="k", kind=kind, name="n", qualified_name="n", path="p", meta=meta)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("built", "group"),
    [
        (node(NodeKind.TEST), "test"),
        (node(NodeKind.FILE, is_test=True), "test"),
        (node(NodeKind.FILE, is_test=False), "caller"),
        (node(NodeKind.ROUTE), "public_api"),
        (node(NodeKind.FUNCTION, public=True), "public_api"),
        (node(NodeKind.CLASS), "caller"),
        (node(NodeKind.METHOD), "caller"),
        (node(NodeKind.TEST, public=True), "test"),
    ],
)
def test_groups(built: Node, group: str) -> None:
    assert group_of(built) == group


def test_ranking_orders_by_score_then_best_then_depth_then_key() -> None:
    class Item:
        def __init__(self, key: str, score: float, best: float, depth: int) -> None:
            self.symbol_key, self.score, self.best_score, self.depth = key, score, best, depth

    items = [
        Item("d", 0.5, 0.5, 1),
        Item("c", 0.5, 0.5, 2),
        Item("b", 0.5, 0.4, 1),
        Item("a", 0.9, 0.1, 3),
        Item("e", 0.5, 0.5, 1),
    ]
    assert [i.symbol_key for i in ranked(items)] == ["a", "d", "e", "c", "b"]
    assert count_by_group(["test", "test", "caller"]) == {"test": 2, "public_api": 0, "caller": 1}


def test_a_report_groups_and_ranks_what_a_change_reaches() -> None:
    report = analyze(TOKENIZE)
    assert report.targets == [TOKENIZE]
    assert [i.symbol_key for i in report.items] == [PARSE, MAIN, TEST_PARSE]
    assert [i.symbol_key for i in report.group("public_api")] == [PARSE]
    assert [i.symbol_key for i in report.group("test")] == [TEST_PARSE]
    assert [i.symbol_key for i in report.group("caller")] == [MAIN]
    assert report.stats.by_group == {"test": 1, "public_api": 1, "caller": 1}
    parse = report.items[0]
    assert parse.depth == 1 and parse.score == pytest.approx(0.6 * 0.94)
    assert [(s.src, s.dst, s.kind) for s in parse.best_path] == [(TOKENIZE, PARSE, "calls")]
    # the evidence is the call site in the caller: `return _tokenize(text)`
    (ref,) = parse.evidence
    assert (ref.path, ref.start_line, ref.symbol_key) == ("pkg/core.py", 6, PARSE)
    (test_ref,) = report.group("test")[0].evidence
    assert (test_ref.path, test_ref.start_line) == ("tests/test_core.py", 5)


def test_scores_never_increase_down_the_ranking() -> None:
    report = analyze(PARSE)
    scores = [item.score for item in report.items]
    assert scores == sorted(scores, reverse=True)
    assert all(item.score >= report.params.min_score for item in report.items)


def test_an_exported_top_level_symbol_reaches_importing_files_and_test_files() -> None:
    report = analyze(PARSE)
    keys = {item.symbol_key for item in report.items}
    assert {MAIN, TEST_PARSE, "file:pkg/cli.py", "file:tests/test_core.py"} <= keys
    test_file = next(i for i in report.items if i.symbol_key == "file:tests/test_core.py")
    assert test_file.group == "test"
    assert test_file.best_path[0].kind == "contains"
    assert test_file.best_path[0].src == PARSE
    assert test_file.best_path[1].kind in {"imports", "tested_by"}
    # a private helper does not: only its callers' chain spreads
    assert not any(i.kind == NodeKind.FILE for i in analyze(TOKENIZE).items)


def test_a_method_change_reaches_its_override() -> None:
    report = analyze(BASE_RUN)
    child = next(i for i in report.items if i.symbol_key == CHILD_RUN)
    assert child.best_path[-1].kind == "overrides"
    assert child.evidence and child.evidence[0].start_line == 15


def test_limit_truncates_and_min_score_filters() -> None:
    report = analyze(PARSE, limit=1)
    assert len(report.items) == 1 and report.stats.truncated
    assert report.stats.items > 1
    high = analyze(TOKENIZE, min_score=0.5)
    assert [i.symbol_key for i in high.items] == [PARSE]


def test_an_unknown_symbol_is_not_found() -> None:
    with pytest.raises(NodeNotFound):
        analyze("py:pkg/core.py:missing:function")
