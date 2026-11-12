"""Dev/test splits, the frozen test hash, and digests (BEN-10, BEN-11).

Questions are split by (repo, type): each group is sorted by ID, shuffled with the repo's seed,
and its first ``round(0.3 * n)`` questions go to dev (at least one when the group has two or
more). The rest are test. ``splits.json`` records both ID lists, the sha256 of the test
questions, and whether the test split is frozen. Once it is frozen, any change to a test
question needs a new benchmark version.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from repoatlas.bench.schema import Question
from repoatlas.canonical import canonical_json

DEV_FRACTION = 0.3


class Splits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark_version: str
    seeds: dict[str, int]  # repo → generation seed (also the split seed)
    frozen: bool
    questions_sha256: str
    test_sha256: str
    dev: list[str]
    test: list[str]
    counts: dict[str, dict[str, int]]  # repo → type → questions


def questions_text(questions: Sequence[Question]) -> str:
    """The canonical ``questions.jsonl``: one canonical JSON line per question, sorted by ID."""
    return "".join(canonical_json(q) + "\n" for q in sorted(questions, key=lambda q: q.id))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def questions_digest(questions: Sequence[Question]) -> str:
    return sha256_text(questions_text(questions))


def split_digest(questions: Sequence[Question]) -> str:
    """sha256 of the canonical test questions only."""
    return sha256_text(questions_text([q for q in questions if q.split == "test"]))


def dev_count(n: int) -> int:
    return max(1, round(DEV_FRACTION * n)) if n >= 2 else 0


def assign_splits(questions: Sequence[Question], seeds: dict[str, int]) -> list[Question]:
    """The same questions with ``split`` set, stratified by repo and type."""
    groups: dict[tuple[str, str], list[Question]] = defaultdict(list)
    for q in sorted(questions, key=lambda q: q.id):
        groups[(q.repo_id, q.type)].append(q)
    split_of: dict[str, str] = {}
    for (repo, qtype), group in sorted(groups.items()):
        order = [q.id for q in group]
        random.Random(f"{seeds[repo]}:{repo}:{qtype}:split").shuffle(order)
        dev = set(order[: dev_count(len(order))])
        for qid in order:
            split_of[qid] = "dev" if qid in dev else "test"
    return [
        q.model_copy(update={"split": split_of[q.id]})
        for q in sorted(questions, key=lambda q: q.id)
    ]


def make_splits(questions: Sequence[Question], seeds: dict[str, int], *, frozen: bool) -> Splits:
    versions = sorted({q.benchmark_version for q in questions})
    if len(versions) != 1:
        raise ValueError(f"questions must share one benchmark version, found {versions}")
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for q in questions:
        counts[q.repo_id][q.type] += 1
    ordered = sorted(questions, key=lambda q: q.id)
    return Splits(
        benchmark_version=versions[0],
        seeds=dict(sorted(seeds.items())),
        frozen=frozen,
        questions_sha256=questions_digest(ordered),
        test_sha256=split_digest(ordered),
        dev=[q.id for q in ordered if q.split == "dev"],
        test=[q.id for q in ordered if q.split == "test"],
        counts={repo: dict(sorted(c.items())) for repo, c in sorted(counts.items())},
    )


def check_frozen(splits: Splits, questions: Sequence[Question]) -> str | None:
    """Why ``questions`` break the frozen test split, or None if they keep it."""
    if not splits.frozen:
        return None
    versions = {q.benchmark_version for q in questions}
    if versions != {splits.benchmark_version}:
        return None  # a new version may change anything
    if [q.id for q in sorted(questions, key=lambda q: q.id) if q.split == "test"] != splits.test:
        return "the test question IDs changed"
    if split_digest(questions) != splits.test_sha256:
        return "a test question changed"
    return None
