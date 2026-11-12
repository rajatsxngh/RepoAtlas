from __future__ import annotations

from collections import Counter

from repoatlas.bench.audit import Sheet, render_sheet, sample
from tests.unit.bench.conftest import MINIPY_COMMIT, fixture_facts
from tests.unit.bench.test_split_determinism import build

URL = "https://github.com/example/minipy"


def test_sample_is_seeded_spread_and_skips_flow() -> None:
    questions = build()
    picked = sample(questions, 0.5, seed=1)
    generated = [q for q in questions if q.type != "flow"]
    assert len(picked) == -(-len(generated) // 2)
    assert all(q.type != "flow" for q in picked)
    assert picked == sample(questions, 0.5, seed=1)
    assert len(Counter(q.type for q in picked)) == len({q.type for q in generated})
    assert [q.id for q in picked] == sorted(q.id for q in picked)


def test_sheet_links_and_classifies_mentions() -> None:
    questions = build()
    facts, files = fixture_facts("minipy", "minipy", MINIPY_COMMIT)
    sheets = {"minipy": Sheet(URL + ".git", MINIPY_COMMIT, files, facts)}
    picked = [q for q in questions if q.type != "flow"]
    text = render_sheet(questions, picked, fraction=1.0, seed=1, sheets=sheets)
    assert text.startswith("# Benchmark 1.0.0 audit")
    assert f"{URL}/blob/{MINIPY_COMMIT}/src/mini/util.py#L1" in text
    assert text.count("- [ ] Matches the code at the pinned commit") == len(picked)
    callers = next(q for q in picked if q.type == "callers" and "`helper`" in q.text)
    section = text.split(f"## {callers.id}")[1].split("\n## ")[0]
    assert "| definition |" in section
    assert "import (not a call)" in section  # core.py line 1
    assert "counted: caller `py:src/mini/util.py:twice:function`" in section
    tests_reach = [q for q in picked if q.notes.startswith("template: tests-reach-1")]
    for q in tests_reach:
        section = text.split(f"## {q.id}")[1].split("\n## ")[0]
        assert "calls it directly" in section or "which calls it at" in section
    owner = [q for q in picked if q.notes.startswith("template: owner-modules")]
    for q in owner:
        assert "git log --no-merges" in text.split(f"## {q.id}")[1]
