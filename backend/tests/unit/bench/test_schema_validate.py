from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from repoatlas.bench.repos import RepoFiles
from repoatlas.bench.schema import (
    AnswerSetTruth,
    FlowTruth,
    ImpactTruth,
    LookupTruth,
    Question,
    UnanswerableTruth,
)
from repoatlas.bench.validate import check_location, parse_questions, validate
from repoatlas.canonical import canonical_json
from repoatlas.ingest.source import MemorySourceTree

COMMIT = "a" * 40
CORE = b"class Context:\n    def invoke(self):\n        pass\n\ndef main():\n    pass\n"
TEST = b"def test_main():\n    main()\n"
KEY_INVOKE = "py:src/core.py:Context.invoke:method"
KEY_MAIN = "py:src/core.py:main:function"
KEY_CTX = "py:src/core.py:Context:class"


def files() -> dict[str, RepoFiles]:
    tree = MemorySourceTree({"src/core.py": CORE, "tests/test_core.py": TEST})
    return {"demo": RepoFiles("demo", COMMIT, tree)}


def loc(key: str, line: int, path: str = "src/core.py") -> dict[str, Any]:
    return {"symbol_key": key, "path": path, "line": line}


def raw(qtype: str, truth: dict[str, Any], n: int = 1, **extra: Any) -> dict[str, Any]:
    data = {
        "id": f"demo-{qtype}-{n:03d}",
        "benchmark_version": "1.0.0",
        "repo_id": "demo",
        "commit": COMMIT,
        "type": qtype,
        "text": "Where is it?",
        "ground_truth": truth,
        "split": "dev",
        "provenance": "scip",
    }
    return {**data, **extra}


def q(qtype: str, truth: dict[str, Any], n: int = 1, **extra: Any) -> Question:
    return Question.model_validate(raw(qtype, truth, n, **extra))


@pytest.mark.parametrize(
    ("qtype", "truth", "model"),
    [
        ("lookup", {"definition": loc(KEY_INVOKE, 2)}, LookupTruth),
        ("callers", {"answers": [loc(KEY_MAIN, 5)]}, AnswerSetTruth),
        ("multihop", {"answers": [loc(KEY_MAIN, 5)]}, AnswerSetTruth),
        (
            "impact",
            {
                "target": loc(KEY_MAIN, 5),
                "static": [],
                "real_failing_tests": [{"test_id": "t::x", "symbol_key": None}],
            },
            ImpactTruth,
        ),
        (
            "flow",
            {
                "must_cite": [loc(KEY_INVOKE, 2), loc(KEY_MAIN, 5), loc(KEY_CTX, 1)],
                "reference_outline": "main calls invoke",
            },
            FlowTruth,
        ),
        ("unanswerable", {"expected": "refusal"}, UnanswerableTruth),
    ],
)
def test_each_type_gets_its_ground_truth_model(
    qtype: str, truth: dict[str, Any], model: type
) -> None:
    question = q(qtype, truth)
    assert isinstance(question.ground_truth, model)
    assert Question.model_validate_json(canonical_json(question)) == question


def test_ground_truth_must_match_the_type() -> None:
    with pytest.raises(ValidationError):
        q("lookup", {"answers": [loc(KEY_MAIN, 5)]})
    with pytest.raises(ValidationError):
        q("unanswerable", {"definition": loc(KEY_MAIN, 5)})


def test_flow_must_cite_three_to_eight() -> None:
    with pytest.raises(ValidationError):
        q("flow", {"must_cite": [loc(KEY_MAIN, 5)], "reference_outline": "x"})


@pytest.mark.parametrize(
    "extra",
    [
        {"id": "demo-lookup-1"},
        {"id": "other-lookup-001"},
        {"id": "demo-callers-001"},
        {"commit": "abc"},
        {"benchmark_version": "1.0"},
        {"split": "train"},
        {"provenance": "repoatlas"},
        {"surprise": 1},
    ],
)
def test_bad_fields_are_rejected(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        q("lookup", {"definition": loc(KEY_INVOKE, 2)}, **extra)


def test_locations() -> None:
    impact = q(
        "impact",
        {"target": loc(KEY_MAIN, 5), "static": [loc(KEY_INVOKE, 2)], "real_failing_tests": None},
    )
    assert [x.line for x in impact.locations()] == [5, 2]
    assert q("unanswerable", {"expected": "refusal"}).locations() == []


def test_valid_benchmark_has_no_problems() -> None:
    questions = [
        q("callers", {"answers": [loc(KEY_INVOKE, 2), loc(KEY_MAIN, 5)]}),
        q("lookup", {"definition": loc(KEY_INVOKE, 2)}),
        q("multihop", {"answers": [loc("file:tests/test_core.py", 1, "tests/test_core.py")]}),
    ]
    assert validate(questions, files()) == []


@pytest.mark.parametrize(
    ("location", "message"),
    [
        (loc(KEY_INVOKE, 3), "does not contain 'invoke'"),
        (loc(KEY_INVOKE, 99), "does not exist"),
        (loc(KEY_INVOKE, 2, "src/other.py"), "is not in src/other.py"),
        (loc("py:src/other.py:x:function", 1, "src/other.py"), "does not exist"),
        (loc("not-a-key", 1), "Not a symbol key"),
        (loc("file:src/core.py", 2), "must point at line 1"),
        (loc("file:nope.py", 1, "nope.py"), "does not exist"),
    ],
)
def test_bad_locations(location: dict[str, Any], message: str) -> None:
    from repoatlas.bench.schema import Location

    error = check_location(Location.model_validate(location), files()["demo"])
    assert error is not None and message in error


def test_test_titles_are_not_name_checked() -> None:
    from repoatlas.bench.schema import Location

    title = Location(symbol_key="ts:src/core.py:suite > does it:test", path="src/core.py", line=1)
    assert check_location(title, files()["demo"]) is None


def test_consistency_problems() -> None:
    questions = [
        q("lookup", {"definition": loc(KEY_INVOKE, 2)}, n=2),
        q("lookup", {"definition": loc(KEY_INVOKE, 2)}, n=1, commit="b" * 40),
        q(
            "callers",
            {"answers": [loc(KEY_MAIN, 5), loc(KEY_INVOKE, 2)]},
            benchmark_version="1.1.0",
        ),
        q(
            "lookup",
            {"definition": loc(KEY_INVOKE, 2)},
            n=1,
            repo_id="ghost",
            id="ghost-lookup-001",
        ),
    ]
    messages = [str(p) for p in validate(questions, files())]
    assert "questions.jsonl: questions are not sorted by ID" in messages
    assert any("mixed versions" in m for m in messages)
    assert any("commit is not the pinned" in m for m in messages)
    assert any("answers are not sorted and unique" in m for m in messages)
    assert "ghost-lookup-001: unknown repo ghost" in messages


def test_duplicate_ids() -> None:
    one = q("lookup", {"definition": loc(KEY_INVOKE, 2)})
    assert any("duplicate ID" in str(p) for p in validate([one, one], files()))


def test_parse_questions_reports_lines_and_non_canonical_json() -> None:
    good = canonical_json(q("lookup", {"definition": loc(KEY_INVOKE, 2)}))
    pretty = good.replace(',"', ', "', 1)
    questions, problems = parse_questions("\n".join([good, "{bad json", pretty, ""]))
    assert len(questions) == 2
    assert problems[0].where == "line 2"
    assert problems[1].message == "line is not canonical JSON"


def test_validate_command(tmp_path: Any) -> None:
    from typer.testing import CliRunner

    from repoatlas.cli import app

    path = tmp_path / "questions.jsonl"
    path.write_text("", encoding="utf-8")
    result = CliRunner().invoke(app, ["bench", "validate", "--questions", str(path)])
    assert result.exit_code == 0, result.output
    assert "0 questions valid" in result.output
    path.write_text(
        canonical_json(q("lookup", {"definition": loc(KEY_INVOKE, 2)})) + "\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["bench", "validate", "--questions", str(path)])
    assert result.exit_code == 1
    assert "unknown repo demo" in result.output
