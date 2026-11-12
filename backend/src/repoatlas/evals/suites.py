"""The default suite, ``benchmark``: M09's questions and splits, scored by ``score_benchmark``.

The split ``dev`` or ``test`` comes from ``splits.json``; ``all`` is both. Loading checks that
the test split still matches its frozen hash, so a run can never score a changed test set.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from repoatlas.bench.repos import benchmark_dir
from repoatlas.bench.split import Splits, check_frozen, questions_digest
from repoatlas.bench.validate import read_questions
from repoatlas.evals.registry import EvalItem, register_suite
from repoatlas.evals.scorers import score_benchmark

SPLITS = ("dev", "test", "all")


class SuiteError(ValueError):
    pass


@cache
def _load(root: Path) -> tuple[Splits, list[EvalItem]]:
    splits = Splits.model_validate(json.loads((root / "splits.json").read_text(encoding="utf-8")))
    questions = read_questions(root / "questions.jsonl")
    problem = check_frozen(splits, questions)
    if problem:
        raise SuiteError(f"The benchmark's test split doesn't match its frozen hash: {problem}")
    items = [
        EvalItem(
            id=q.id,
            type=q.type,
            text=q.text,
            repo_id=q.repo_id,
            commit=q.commit,
            split=q.split,
            truth=q.ground_truth,
        )
        for q in sorted(questions, key=lambda q: q.id)
    ]
    return splits, items


def load_benchmark(split: str, root: Path | None = None) -> list[EvalItem]:
    if split not in SPLITS:
        raise SuiteError(f"The split must be one of {', '.join(SPLITS)}.")
    _, items = _load(root or benchmark_dir())
    return [item for item in items if split == "all" or item.split == split]


def benchmark_info(root: Path | None = None) -> dict[str, Any]:
    splits, _ = _load(root or benchmark_dir())
    questions = read_questions((root or benchmark_dir()) / "questions.jsonl")
    return {
        "benchmark_version": splits.benchmark_version,
        "questions_sha256": questions_digest(questions),
        "test_sha256": splits.test_sha256,
        "test_frozen": splits.frozen,
    }


register_suite(
    "benchmark", load_benchmark, {"*": score_benchmark}, info=benchmark_info, replace=True
)
