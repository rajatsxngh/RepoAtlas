from __future__ import annotations

import pytest

from repoatlas.bench.facts import Facts
from repoatlas.bench.flow import flow_questions, parse_flow_file
from repoatlas.bench.generate import gen_unanswerable
from repoatlas.bench.repos import benchmark_dir
from repoatlas.bench.schema import FlowTruth, Location, UnanswerableTruth
from tests.unit.bench.conftest import MINIPY_COMMIT, fixture_facts

CORE = "py:src/mini/core.py:"
UTIL = "py:src/mini/util.py:"

FLOW = """# minipy flow questions

- Written by: Ada
- Reviewed by: pending

## minipy-flow-001

**Question:** How does `entry` compute its result?

**Must cite:**
- `py:src/mini/core.py:entry:function`
- `py:src/mini/core.py:make:function`
- `py:src/mini/util.py:helper:function`

**Reference outline:**
1. `entry` builds a `Base` with `make`.
2. It adds `Base.run()` to `helper(2)`.
"""


def locate(facts: Facts):
    def inner(key: str) -> Location | None:
        return facts.location(key) if key in facts.definitions else None

    return inner


def test_parse_flow_file() -> None:
    flow = parse_flow_file("minipy", FLOW)
    assert (flow.written_by, flow.reviewed_by, flow.reviewed) == ("Ada", "pending", False)
    [draft] = flow.questions
    assert draft.id == "minipy-flow-001"
    assert draft.text == "How does `entry` compute its result?"
    assert draft.must_cite == [
        CORE + "entry:function",
        CORE + "make:function",
        UTIL + "helper:function",
    ]
    assert draft.reference_outline.startswith("1. `entry` builds")
    assert "helper(2)" in draft.reference_outline
    assert parse_flow_file("minipy", FLOW.replace("pending", "Grace")).reviewed


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("- Reviewed by: pending\n", ""), "header needs"),
        (("**Question:**", "**Q:**"), r"needs \*\*Question:\*\*"),
        (("## minipy-flow-001", "## other-flow-001"), "must be minipy-flow-NNN"),
        (("- `py:src/mini/util.py:helper:function`\n", ""), "at least 3"),
    ],
)
def test_malformed_flow_files(change: tuple[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_flow_file("minipy", FLOW.replace(*change))


def test_flow_questions(minipy: Facts) -> None:
    flow = parse_flow_file("minipy", FLOW)
    [q] = flow_questions(flow, commit=MINIPY_COMMIT, version="1.0.0", locate=locate(minipy))
    assert isinstance(q.ground_truth, FlowTruth)
    assert [(c.path, c.line) for c in q.ground_truth.must_cite] == [
        ("src/mini/core.py", 21),
        ("src/mini/core.py", 17),
        ("src/mini/util.py", 1),
    ]
    assert q.provenance == "human"
    assert q.notes == "Written by Ada reading the code; reviewed by pending."


def test_flow_questions_reject_unknown_symbols(minipy: Facts) -> None:
    flow = parse_flow_file("minipy", FLOW.replace("make:function", "build:function"))
    with pytest.raises(ValueError, match="build:function is not a symbol"):
        flow_questions(flow, commit=MINIPY_COMMIT, version="1.0.0", locate=locate(minipy))


def test_the_template_parses() -> None:
    template = benchmark_dir() / "flow" / "_template.md"
    flow = parse_flow_file("repo", template.read_text(encoding="utf-8"))
    assert len(flow.questions) == 1 and not flow.reviewed


def test_gen_unanswerable() -> None:
    facts, files = fixture_facts("minipy", "minipy", MINIPY_COMMIT)
    corpus = "\n".join(files.text(p) for p in files.paths() if p.endswith(".py"))
    questions = gen_unanswerable(facts, corpus, seed=1, display_repo="minipy")
    assert [q.id for q in questions] == ["minipy-unanswerable-001", "minipy-unanswerable-002"]
    absent, outside = questions
    assert isinstance(absent.ground_truth, UnanswerableTruth)
    name = absent.text.split("`")[1]
    leaf = name.rsplit(".", 1)[-1]
    assert leaf not in corpus and absent.provenance == "scip"
    assert (outside.provenance == "human" and "minipy" in outside.text) or "MINIPY" in outside.text
    again = gen_unanswerable(facts, corpus, seed=1, display_repo="minipy")
    assert again == questions
    assert gen_unanswerable(facts, corpus, seed=2, display_repo="minipy") != questions


def test_unanswerable_names_never_exist() -> None:
    facts, _ = fixture_facts("minipy", "minipy", MINIPY_COMMIT)
    # A corpus that contains every invented name for most bases forces retries, never a clash.
    corpus = "reset_helper flush_helper prune_helper rebind_helper"
    for seed in range(20):
        q = gen_unanswerable(facts, corpus, seed=seed, display_repo="minipy", count=1)[0]
        assert q.text.split("`")[1].rsplit(".", 1)[-1] not in corpus
