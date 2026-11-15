"""``docs/results.md`` and the README publish numbers. Every one of them must be the number in
its report file (LCH-3, decision 0025): the tables are rebuilt here from the reports and
compared with the page, and nothing pending may be worded as measured."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / "eval" / "reports"
PAGE = (ROOT / "docs" / "results.md").read_text(encoding="utf-8")
SYSTEMS = ("closed_book", "bm25_only", "vector_only", "hybrid_graphrag")


def report(system: str) -> dict[str, Any]:
    path = REPORTS / f"2026-11-12-{system}-test.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert data["complete"] and data["split"] == "test" and data["runs"] == 3
    assert data["suite_info"]["test_frozen"] is True
    return data


def interval(stat: dict[str, float]) -> str:
    return f"{stat['mean']:.3f} [{stat['low']:.3f}, {stat['high']:.3f}]"


def table_rows(path: Path) -> list[str]:
    """The data rows of a report's first table."""
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("|")]
    return rows[2:]


def test_the_main_table_is_the_reports() -> None:
    reports = {system: report(system) for system in SYSTEMS}
    types = reports["hybrid_graphrag"]["by_type"]
    for position, kind in enumerate(types):
        scores = " | ".join(
            f"{reports[system]['by_type'][position]['primary']['mean']:.3f}" for system in SYSTEMS
        )
        row = f"| {kind['type']} ({kind['primary_metric']}) | {kind['questions']} | {scores} |"
        assert row in PAGE, row
    overall = " | ".join(interval(reports[system]["overall"]) for system in SYSTEMS)
    assert f"| **all** | 90 | {overall} |" in PAGE
    cost = " | ".join(f"${reports[system]['cost_per_question_usd']:.4f}" for system in SYSTEMS)
    assert f"| cost per question | | {cost} |" in PAGE
    latency = " | ".join(
        f"{reports[system]['aggregates']['latency_p50_s']:.1f} s / "
        f"{reports[system]['aggregates']['latency_p95_s']:.1f} s"
        for system in SYSTEMS
    )
    assert f"| latency p50 / p95 | | {latency} |" in PAGE


def test_the_benchmark_digest_and_spend_are_the_reports() -> None:
    reports = {system: report(system) for system in SYSTEMS}
    digest = reports["hybrid_graphrag"]["suite_info"]["test_sha256"]
    assert f"`{digest[:8]}…{digest[-7:]}`" in PAGE
    spend = (
        f"closed book ${reports['closed_book']['total_cost_usd']:.2f}, "
        f"keyword ${reports['bm25_only']['total_cost_usd']:.2f}, "
        f"vector ${reports['vector_only']['total_cost_usd']:.2f},\n"
        f"hybrid ${reports['hybrid_graphrag']['total_cost_usd']:.2f}"
    )
    assert spend in PAGE
    refusals = [reports[s]["refusals"]["answerable_refused_evidence"] for s in SYSTEMS[1:]]
    answerable = reports["hybrid_graphrag"]["refusals"]["answerable"]
    assert (
        f"refused {refusals[2]} of {answerable} answerable\n  question runs for lack of "
        f"evidence (vector-only {refusals[1]}, keyword-only {refusals[0]})" in PAGE
    )


def test_the_paired_comparison_is_the_compare_report() -> None:
    rows = table_rows(REPORTS / "2026-11-12-compare-hybrid_graphrag-vs-vector_only-test.md")
    assert len(rows) == 7
    for row in rows:
        without_probability = row.rsplit(" | ", 1)[0] + " |"
        assert without_probability in PAGE, without_probability
    for other, wanted in (("bm25_only", "+0.129 [+0.072, +0.190]"), ("closed_book", "+0.350")):
        last = table_rows(REPORTS / f"2026-11-12-compare-hybrid_graphrag-vs-{other}-test.md")[-1]
        assert wanted in last and wanted in PAGE


def test_the_impact_table_is_the_intervals_report() -> None:
    rows = table_rows(REPORTS / "2026-11-13-impact_engine-intervals-test.md")
    shown = [row for row in rows if "recall@" not in row][:6]
    assert len(shown) == 6
    for row in shown:
        assert row in PAGE, row
    data = json.loads(
        (REPORTS / "2026-11-13-impact_engine-intervals-test.json").read_text(encoding="utf-8")
    )
    recall = next(item for item in data["intervals"] if item["metric"] == "real_recall")
    assert f"is {interval(recall)}" in PAGE

    versus = table_rows(REPORTS / "2026-11-13-compare-impact_engine-vs-hybrid_graphrag-test.md")
    impact = next(row for row in versus if row.startswith("| impact |"))
    assert "| 0.486 | 0.366 | -0.120 [-0.355, +0.117] |" in impact
    assert "0.120 [-0.117, +0.355] in the engine's favour" in PAGE

    unknown = (REPORTS / "2026-11-13-impact_engine-unknown-test.md").read_text(encoding="utf-8")
    assert "Confirmed failing tests: 704; ranked 30, in the unknown bucket only 1, missed 673" in (
        unknown
    )
    assert "Of 704 confirmed failing tests it ranks 30 and misses\n  673" in PAGE


def test_every_linked_report_exists() -> None:
    links = re.findall(r"\]\((\.\./eval/[^)#]+)\)", PAGE)
    assert len(links) >= 6
    for link in links:
        assert (ROOT / "docs" / link).resolve().exists(), link


def test_pending_work_is_never_worded_as_measured() -> None:
    """Agent and multi-agent modes have no live numbers; the page may name them only as
    pending."""
    before, pending = PAGE.split("## Pending (not measured yet)")
    for mode in ("agent mode", "multi-agent"):
        assert mode not in before.lower(), mode
    assert "no accuracy or cost numbers" in pending
    for item in ("| 13 |", "| 17 |", "| 20 |", "| 25 |"):
        assert item in pending
    assert not re.search(r"(?i)\b(passed|verified in production|live at)\b", PAGE)
