from __future__ import annotations

import pytest
from pydantic import JsonValue

from repoatlas.bench.facts import Definition, Facts
from repoatlas.evals.calibration import (
    MIN_JUDGED,
    Calibration,
    RepoCalibration,
    Tally,
    calibrate_graph,
    current_confidence,
    fit_confidences,
    render_calibration,
    rows,
    wilson,
)
from repoatlas.graph.model import CodeGraph, Edge, EdgeKind, Node, NodeKind, Provenance

A = "py:src/a.py:a:function"
B = "py:src/b.py:b:function"
C = "py:src/b.py:c:function"
T = "py:tests/t.py:test_a:test"
UNKNOWN = "py:src/x.py:x:function"


def definition(key: str, path: str) -> Definition:
    name = key.split(":")[2]
    return Definition(
        key=key,
        path=path,
        line=1,
        name=name,
        qualname=name,
        kind="function",
        scip_symbol=f"scip {name}",
        is_test_file=path.startswith("tests"),
    )


def facts() -> Facts:
    f = Facts(repo_id="r", commit="c" * 40)
    for key, path in ((A, "src/a.py"), (B, "src/b.py"), (C, "src/b.py"), (T, "tests/t.py")):
        f.definitions[key] = definition(key, path)
    f.callers[B].add(A)  # a calls b
    f.callers[A].add(T)  # the test calls a
    f.callers[C].add(B)  # b calls c (RepoAtlas misses it)
    f.file_references["src/a.py"].add(B)
    return f


def node(key: str, path: str, kind: str = "function") -> Node:
    name = key.rsplit("/", 1)[-1] if key.startswith("file:") else key.split(":")[2]
    return Node(key=key, kind=NodeKind(kind), name=name, qualified_name=name, path=path)


def call(src: str, dst: str, tier: str, candidates: int = 1) -> Edge:
    meta: dict[str, JsonValue] = {"tier": tier}
    if candidates > 1:
        meta["candidates"] = candidates
    return Edge(
        src=src,
        dst=dst,
        kind=EdgeKind.CALLS,
        provenance=Provenance.NAME_MATCH if tier in ("T5", "T6") else Provenance.TREE_SITTER,
        confidence=0.5,
        meta=meta,
    )


def graph() -> CodeGraph:
    nodes = {
        k: node(k, p)
        for k, p in ((A, "src/a.py"), (B, "src/b.py"), (C, "src/b.py"), (T, "tests/t.py"))
    }
    nodes["file:src/a.py"] = node("file:src/a.py", "src/a.py", "file")
    nodes["file:src/b.py"] = node("file:src/b.py", "src/b.py", "file")
    edges = [
        call(A, B, "T1"),  # confirmed
        call(A, C, "T6", candidates=2),  # wrong
        call(B, UNKNOWN, "T5"),  # SCIP doesn't know x: unjudged
        Edge(
            src=A,
            dst=T,
            kind=EdgeKind.TESTED_BY,
            provenance=Provenance.IMPORT_RESOLVER,
            confidence=0.9,
        ),
        Edge(
            src=B, dst=T, kind=EdgeKind.TESTED_BY, provenance=Provenance.CONVENTION, confidence=0.6
        ),
        Edge(
            src="file:src/a.py",
            dst="file:src/b.py",
            kind=EdgeKind.IMPORTS,
            provenance=Provenance.IMPORT_RESOLVER,
            confidence=1.0,
        ),
        Edge(
            src="file:src/b.py",
            dst="file:src/a.py",
            kind=EdgeKind.IMPORTS,
            provenance=Provenance.IMPORT_RESOLVER,
            confidence=1.0,
        ),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def test_edges_are_judged_against_scip_facts() -> None:
    result = calibrate_graph("r", "c" * 40, graph(), facts())
    t = result.tallies
    assert t["calls|tier|T1"] == Tally(edges=1, judged=1, confirmed=1, candidates=1)
    assert t["calls|tier|T6"] == Tally(edges=1, judged=1, confirmed=0, candidates=2)
    assert t["calls|tier|T5"] == Tally(edges=1, judged=0, confirmed=0, candidates=0)
    assert t["tested_by|provenance|import_resolver"].confirmed == 1
    assert t["tested_by|provenance|convention"] == Tally(
        edges=1, judged=1, confirmed=0, candidates=1
    )
    assert t["imports|provenance|import_resolver"] == Tally(
        edges=2, judged=2, confirmed=1, candidates=2
    )
    # SCIP pairs: a→b (found), t→a (a tested_by edge, not a call), b→c (missed)
    assert (result.scip_call_pairs, result.found_call_pairs) == (3, 1)
    assert result.call_recall == pytest.approx(1 / 3)


def test_rows_pool_repos_and_show_current_confidences() -> None:
    one = calibrate_graph("r", "c" * 40, graph(), facts())
    table = rows([one, one])
    t1 = next(r for r in table if r.group == "T1")
    assert (t1.judged, t1.confirmed, t1.precision, t1.current) == (2, 2, 1.0, 0.94)
    assert t1.low is not None and t1.low < 1.0
    t5 = next(r for r in table if r.group == "T5")
    assert t5.precision is None and t5.low is None
    assert current_confidence("tested_by", "provenance", "convention") == 0.6
    assert current_confidence("calls", "tier", "T9") is None


def test_fitting_needs_enough_edges_and_scales_t6_by_candidates() -> None:
    def repo(**tallies: Tally) -> RepoCalibration:
        return RepoCalibration(
            repo_id="r",
            commit="c",
            misresolved_references=0,
            scip_call_pairs=0,
            found_call_pairs=0,
            tallies={k.replace("__", "|"): v for k, v in tallies.items()},
        )

    big = repo(
        calls__tier__T1=Tally(edges=100, judged=98, confirmed=97, candidates=98),
        calls__tier__T6=Tally(edges=60, judged=60, confirmed=20, candidates=150),
        calls__tier__T4=Tally(edges=5, judged=5, confirmed=5, candidates=5),
        tested_by__provenance__convention=Tally(edges=40, judged=40, confirmed=30, candidates=40),
    )
    fitted = fit_confidences([big])
    assert fitted["calls.T1"] == round(98 / 100, 2)
    assert fitted["calls.T6"] == round((21 / 62) * 150 / 60, 2)  # total over ~2.5 candidates
    assert "calls.T4" not in fitted  # fewer than MIN_JUDGED
    assert fitted["tested_by.convention"] == round(31 / 42, 2)
    assert MIN_JUDGED == 20
    perfect = repo(calls__tier__T2=Tally(edges=400, judged=400, confirmed=400, candidates=400))
    assert fit_confidences([perfect])["calls.T2"] == 0.99  # capped


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    low, high = wilson(50, 100) or (0, 0)
    assert low == pytest.approx(0.404, abs=0.002) and high == pytest.approx(0.596, abs=0.002)
    assert wilson(10, 10)[1] == 1.0  # type: ignore[index]


def test_render_marks_measured_only_tables() -> None:
    one = calibrate_graph("r", "c" * 40, graph(), facts())
    fitted = Calibration(
        repos=[one], rows=rows([one]), fitted={"calls.T1": 0.9}, used_for_fitting=True
    )
    assert "calls.T1: 0.90" in render_calibration(fitted, "Calibration")
    measured = Calibration(repos=[one], rows=rows([one]), used_for_fitting=False)
    assert "not used for fitting" in render_calibration(measured, "Benchmark repos")


def test_edges_from_files_scip_did_not_index_are_unjudged() -> None:
    f = facts()
    del f.definitions[T]  # tests/t.py is not in the index (an indexer that skips tests)
    f.test_symbols[T] = None  # type: ignore[assignment]  # parsed, but SCIP never saw it
    edge = Edge(
        src=T,
        dst=A,
        kind=EdgeKind.CALLS,
        provenance=Provenance.TREE_SITTER,
        confidence=0.9,
        meta={"tier": "T1"},
    )
    result = calibrate_graph("r", "c" * 40, CodeGraph(edges=[edge]), f)
    assert result.tallies["calls|tier|T1"] == Tally(edges=1, judged=0, confirmed=0, candidates=0)


def test_file_level_tested_by_edges_use_file_references() -> None:
    f = facts()
    f.file_references["tests/t.py"].add(A)
    good = Edge(
        src="file:src/a.py",
        dst="file:tests/t.py",
        kind=EdgeKind.TESTED_BY,
        provenance=Provenance.IMPORT_RESOLVER,
        confidence=0.9,
    )
    bad = Edge(
        src="file:src/b.py",
        dst="file:tests/t.py",
        kind=EdgeKind.TESTED_BY,
        provenance=Provenance.IMPORT_RESOLVER,
        confidence=0.9,
    )
    result = calibrate_graph("r", "c" * 40, CodeGraph(edges=[good, bad]), f)
    assert result.tallies["tested_by|provenance|import_resolver"] == Tally(
        edges=2, judged=2, confirmed=1, candidates=2
    )
