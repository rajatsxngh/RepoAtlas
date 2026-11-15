"""The numbers a launch document may quote, formatted from the report files (used by the
tests of the README and the post drafts)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / "eval" / "reports"
LAUNCH = ROOT / "docs" / "launch"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def report(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(read(REPORTS / name))
    return data


def interval(stat: dict[str, float]) -> str:
    return f"{stat['mean']:.3f} [{stat['low']:.3f}, {stat['high']:.3f}]"


def measured() -> dict[str, str]:
    hybrid = report("2026-11-12-hybrid_graphrag-test.json")
    vector = report("2026-11-12-vector_only-test.json")
    closed = report("2026-11-12-closed_book-test.json")
    impact = report("2026-11-13-impact_engine-intervals-test.json")
    recall = next(item for item in impact["intervals"] if item["metric"] == "real_recall")
    lookup = next(kind for kind in closed["by_type"] if kind["type"] == "lookup")
    compare = read(REPORTS / "2026-11-12-compare-hybrid_graphrag-vs-vector_only-test.md")
    overall = re.search(r"\| \*\*all\*\* \| 90 \| \S+ \| \S+ \| (\S+ \[[^\]]+\])", compare)
    multihop = re.search(r"\| multihop \| 15 \| \S+ \| \S+ \| (\S+ \[[^\]]+\])", compare)
    assert overall and multihop
    return {
        "hybrid": interval(hybrid["overall"]),
        "vector": f"{vector['overall']['mean']:.3f}",
        "gain": overall.group(1),
        "multihop": multihop.group(1),
        "real_recall": interval(recall),
        "memory": f"{round(lookup['primary']['mean'] * 100)}%",
        "cost": f"${hybrid['cost_per_question_usd']:.4f}",
        "questions": str(hybrid["overall"]["n"]),
    }
