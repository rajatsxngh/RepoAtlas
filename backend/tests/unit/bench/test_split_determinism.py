from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoatlas.bench import repos
from repoatlas.bench.build import build_questions, locator
from repoatlas.bench.flow import parse_flow_file
from repoatlas.bench.ownership import Ownership
from repoatlas.bench.split import (
    assign_splits,
    check_frozen,
    dev_count,
    make_splits,
    questions_digest,
    questions_text,
    split_digest,
)
from repoatlas.canonical import pretty_json
from repoatlas.cli import app
from repoatlas.sandbox.parsed import parse_tree
from tests.unit.bench.conftest import MINIPY_COMMIT, fixture_facts
from tests.unit.bench.test_flow_unanswerable import FLOW
from tests.unit.bench.test_real_impact import RECORDS

OWNERSHIP = Ownership(commits=3, owners={"src/mini/util.py": "Ada"}, file_commits={})


def build(seed: int = 1):
    facts, files = fixture_facts("minipy", "minipy", MINIPY_COMMIT)
    text = "\n".join(files.text(p) for p in files.paths() if p.endswith(".py"))
    questions, _ = build_questions(
        facts,
        seed=seed,
        text=text,
        ownership=OWNERSHIP,
        records=list(RECORDS),
        flow=parse_flow_file("minipy", FLOW),
        locate=locator(facts, parse_tree(files.tree)),
    )
    return assign_splits(questions, {"minipy": seed})


def test_regeneration_is_identical() -> None:
    one, two = build(), build()
    assert questions_text(one) == questions_text(two)
    assert questions_digest(one) == questions_digest(two)
    assert questions_digest(build(seed=2)) != questions_digest(one)


def test_every_type_is_built() -> None:
    types = Counter(q.type for q in build())
    assert set(types) == {"lookup", "callers", "multihop", "flow", "impact", "unanswerable"}


@pytest.mark.parametrize(("n", "dev"), [(0, 0), (1, 0), (2, 1), (4, 1), (6, 2), (10, 3)])
def test_dev_count(n: int, dev: int) -> None:
    assert dev_count(n) == dev


def test_splits_are_stratified_by_repo_and_type() -> None:
    questions = build()
    by_group: dict[tuple[str, str], Counter[str]] = {}
    for q in questions:
        by_group.setdefault((q.repo_id, q.type), Counter())[q.split] += 1
    for (_, _), counts in by_group.items():
        n = sum(counts.values())
        assert counts["dev"] == dev_count(n)
    assert assign_splits(questions, {"minipy": 1}) == questions  # idempotent


def test_make_splits_and_freeze_check() -> None:
    questions = build()
    splits = make_splits(questions, {"minipy": 1}, frozen=True)
    assert splits.test_sha256 == split_digest(questions)
    assert sorted(splits.dev + splits.test) == sorted(q.id for q in questions)
    assert sum(splits.counts["minipy"].values()) == len(questions)
    assert check_frozen(splits, questions) is None
    test_q = next(q for q in questions if q.split == "test")
    dev_q = next(q for q in questions if q.split == "dev")
    edited = [q.model_copy(update={"text": "changed"}) if q == test_q else q for q in questions]
    assert check_frozen(splits, edited) == "a test question changed"
    dev_edit = [q.model_copy(update={"text": "changed"}) if q == dev_q else q for q in questions]
    assert check_frozen(splits, dev_edit) is None  # dev questions may change
    dropped = [q for q in questions if q != test_q]
    assert check_frozen(splits, dropped) == "the test question IDs changed"
    bumped = [q.model_copy(update={"benchmark_version": "1.1.0"}) for q in edited]
    assert check_frozen(splits, bumped) is None
    assert check_frozen(splits.model_copy(update={"frozen": False}), edited) is None


def test_make_splits_needs_one_version() -> None:
    questions = build()
    mixed = [questions[0].model_copy(update={"benchmark_version": "2.0.0"}), *questions[1:]]
    with pytest.raises(ValueError, match="one benchmark version"):
        make_splits(mixed, {"minipy": 1}, frozen=False)


def bench_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewed: bool) -> Path:
    questions = build()
    (tmp_path / "flow").mkdir()
    flow = FLOW if not reviewed else FLOW.replace("Reviewed by: pending", "Reviewed by: Grace")
    (tmp_path / "flow" / "minipy.md").write_text(flow, encoding="utf-8")
    (tmp_path / "questions.jsonl").write_text(questions_text(questions), encoding="utf-8")
    splits = make_splits(questions, {"minipy": 1}, frozen=False)
    (tmp_path / "splits.json").write_text(pretty_json(splits), encoding="utf-8")
    monkeypatch.setattr(repos, "benchmark_dir", lambda: tmp_path)
    return tmp_path


def test_freeze_needs_reviewed_flow_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = bench_folder(tmp_path, monkeypatch, reviewed=False)
    result = CliRunner().invoke(app, ["bench", "split", "--freeze"])
    assert result.exit_code == 1
    assert "not reviewed yet: minipy" in result.output
    result = CliRunner().invoke(app, ["bench", "split"])
    assert result.exit_code == 0, result.output
    assert '"frozen": false' in (folder / "splits.json").read_text(encoding="utf-8")


def test_freeze_and_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = bench_folder(tmp_path, monkeypatch, reviewed=True)
    result = CliRunner().invoke(app, ["bench", "split", "--freeze"])
    assert result.exit_code == 0, result.output
    assert "(frozen)" in result.output
    assert '"frozen": true' in (folder / "splits.json").read_text(encoding="utf-8")
    result = CliRunner().invoke(app, ["bench", "digest"])
    assert result.exit_code == 0, result.output
    assert questions_digest(build()) in result.output
    # A hand edit to a test question is caught.
    lines = (folder / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    test_line = next(i for i, line in enumerate(lines) if '"split":"test"' in line)
    lines[test_line] = lines[test_line].replace('"text":"', '"text":"Edited. ', 1)
    (folder / "questions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["bench", "split"])
    assert result.exit_code == 1
    assert "a test question changed" in result.output


def test_digest_rejects_non_canonical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = bench_folder(tmp_path, monkeypatch, reviewed=False)
    text = (folder / "questions.jsonl").read_text(encoding="utf-8")
    lines = text.splitlines()
    (folder / "questions.jsonl").write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["bench", "digest"])
    assert result.exit_code == 1
