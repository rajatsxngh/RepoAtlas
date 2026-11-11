"""The committed smoke question set (eval/smoke/questions.yaml)."""

from __future__ import annotations

from pathlib import Path

from repoatlas.answers.smoke import load_questions

QUESTIONS = Path(__file__).resolve().parents[3] / "eval" / "smoke" / "questions.yaml"


def test_twenty_questions_fifteen_answerable() -> None:
    smoke = load_questions(QUESTIONS)
    assert smoke.repo == "colinhacks/zod"
    assert len(smoke.commit) == 40
    assert len(smoke.questions) == 20
    assert sum(q.answerable for q in smoke.questions) == 15
    assert len({q.id for q in smoke.questions}) == 20
    for question in smoke.questions:
        assert bool(question.expected_symbols) == question.answerable, question.id
