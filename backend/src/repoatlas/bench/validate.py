"""``repoatlas bench validate``: schema, consistency, and locations at the pinned commits."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from repoatlas.bench.repos import RepoFiles
from repoatlas.bench.schema import AnswerSetTruth, ImpactTruth, Location, Question
from repoatlas.canonical import canonical_json
from repoatlas.parsing.symbols import parse_symbol_key


class Problem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    where: str  # question ID, or "line N" when the line did not parse
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


def parse_questions(text: str) -> tuple[list[Question], list[Problem]]:
    questions: list[Question] = []
    problems: list[Problem] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            question = Question.model_validate_json(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            problems.append(Problem(where=f"line {number}", message=f"{loc}: {first['msg']}"))
            continue
        if canonical_json(question) != raw:
            problems.append(Problem(where=question.id, message="line is not canonical JSON"))
        questions.append(question)
    return questions, problems


def read_questions(path: Path) -> list[Question]:
    """Parse ``questions.jsonl``; raises ``ValueError`` listing every line that fails."""
    questions, problems = parse_questions(path.read_text(encoding="utf-8"))
    fatal = [p for p in problems if p.where.startswith("line ")]
    if fatal:
        raise ValueError("; ".join(str(p) for p in fatal))
    return questions


def key_name(symbol_key: str) -> str | None:
    """The name a definition line must contain (None for TS test titles and file keys)."""
    if symbol_key.startswith("file:"):
        return None
    parts = parse_symbol_key(symbol_key)
    if parts.kind == "test" and parts.lang != "py":
        return None
    name = parts.qualname.rsplit(".", 1)[-1]
    return None if name == "<anon>" else name


def check_location(location: Location, files: RepoFiles) -> str | None:
    """Why ``location`` is wrong at the pinned commit, or None."""
    if location.symbol_key.startswith("file:"):
        if location.symbol_key != f"file:{location.path}" or location.line != 1:
            return f"file key {location.symbol_key} must point at line 1 of its path"
        return None if files.has(location.path) else f"{location.path} does not exist"
    try:
        parts = parse_symbol_key(location.symbol_key)
    except ValueError as exc:
        return str(exc)
    if parts.path != location.path:
        return f"{location.symbol_key} is not in {location.path}"
    text = files.line(location.path, location.line)
    if text is None:
        return f"{location.path}:{location.line} does not exist"
    name = key_name(location.symbol_key)
    if name is not None and name not in text:
        return f"{location.path}:{location.line} does not contain {name!r}"
    return None


def _sorted_unique(locations: list[Location]) -> bool:
    keys = [loc.symbol_key for loc in locations]
    return keys == sorted(set(keys))


def validate(
    questions: list[Question],
    files: Mapping[str, RepoFiles],
    *,
    problems: list[Problem] | None = None,
) -> list[Problem]:
    """Every problem found; an empty list means the benchmark is valid."""
    found = list(problems or [])
    ids = [q.id for q in questions]
    if ids != sorted(ids):
        found.append(Problem(where="questions.jsonl", message="questions are not sorted by ID"))
    versions = sorted({q.benchmark_version for q in questions})
    if len(versions) > 1:
        found.append(Problem(where="questions.jsonl", message=f"mixed versions {versions}"))
    seen: set[str] = set()
    for question in questions:
        where = question.id
        if question.id in seen:
            found.append(Problem(where=where, message="duplicate ID"))
        seen.add(question.id)
        repo = files.get(question.repo_id)
        if repo is None:
            found.append(Problem(where=where, message=f"unknown repo {question.repo_id}"))
            continue
        if question.commit != repo.commit:
            found.append(Problem(where=where, message=f"commit is not the pinned {repo.commit}"))
        for location in question.locations():
            error = check_location(location, repo)
            if error:
                found.append(Problem(where=where, message=error))
        truth = question.ground_truth
        if isinstance(truth, AnswerSetTruth) and not _sorted_unique(truth.answers):
            found.append(Problem(where=where, message="answers are not sorted and unique"))
        if isinstance(truth, ImpactTruth):
            if not _sorted_unique(truth.static):
                found.append(Problem(where=where, message="static set is not sorted and unique"))
            for test in truth.real_failing_tests or []:
                if test.symbol_key is None:
                    continue
                try:
                    parse_symbol_key(test.symbol_key)
                except ValueError as exc:
                    found.append(Problem(where=where, message=str(exc)))
    return found
