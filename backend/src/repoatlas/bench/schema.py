"""The benchmark's question schema (BEN-1).

One ``Question`` per line of ``eval/benchmark/questions.jsonl``. Ground truth names symbols by
symbol key (PRD §7.3) plus the definition location (repo-relative path, 1-based line of the
symbol's name), so system outputs can be matched by key and checked by location.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QuestionType = Literal["lookup", "callers", "multihop", "flow", "impact", "unanswerable"]
Provenance = Literal["scip", "git", "sandbox", "human"]
Split = Literal["dev", "test"]
QUESTION_TYPES: tuple[QuestionType, ...] = (
    "lookup",
    "callers",
    "multihop",
    "flow",
    "impact",
    "unanswerable",
)
ID_RE = re.compile(r"^(?P<repo>[a-z0-9_]+)-(?P<type>[a-z]+)-(?P<n>\d{3})$")
SEMVER = r"^\d+\.\d+\.\d+$"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Location(_Model):
    """A symbol and where it is defined: the path and line of its name."""

    symbol_key: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line: int = Field(ge=1)


class LookupTruth(_Model):
    definition: Location


class AnswerSetTruth(_Model):
    """``callers`` and ``multihop``: the exact set of answers, sorted by symbol key."""

    answers: list[Location] = Field(min_length=1)


class RealFailingTest(_Model):
    test_id: str
    symbol_key: str | None  # None when the test ID did not map to a symbol key


class ImpactTruth(_Model):
    target: Location
    static: list[Location]
    real_failing_tests: list[RealFailingTest] | None  # None when the target was not mutated


class FlowTruth(_Model):
    must_cite: list[Location] = Field(min_length=3, max_length=8)
    reference_outline: str = Field(min_length=1)


class UnanswerableTruth(_Model):
    expected: Literal["refusal"] = "refusal"


GroundTruth = LookupTruth | AnswerSetTruth | ImpactTruth | FlowTruth | UnanswerableTruth
TRUTH_FOR_TYPE: dict[QuestionType, type[BaseModel]] = {
    "lookup": LookupTruth,
    "callers": AnswerSetTruth,
    "multihop": AnswerSetTruth,
    "impact": ImpactTruth,
    "flow": FlowTruth,
    "unanswerable": UnanswerableTruth,
}


class Question(_Model):
    id: str
    benchmark_version: Annotated[str, Field(pattern=SEMVER)]
    repo_id: str
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    type: QuestionType
    text: str = Field(min_length=1)
    ground_truth: GroundTruth
    split: Split
    provenance: Provenance
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _truth_by_type(cls, data: object) -> object:
        """Validate ``ground_truth`` with the model its ``type`` names (no guessing)."""
        if isinstance(data, dict):
            model = TRUTH_FOR_TYPE.get(data.get("type"))  # type: ignore[arg-type]
            truth = data.get("ground_truth")
            if model is not None and isinstance(truth, dict):
                data = {**data, "ground_truth": model.model_validate(truth)}
        return data

    @model_validator(mode="after")
    def _consistent(self) -> Question:
        if not isinstance(self.ground_truth, TRUTH_FOR_TYPE[self.type]):
            raise ValueError(f"{self.type} questions need {TRUTH_FOR_TYPE[self.type].__name__}")
        match = ID_RE.match(self.id)
        if match is None:
            raise ValueError(f"ID {self.id!r} is not <repo>-<type>-<nnn>")
        if (match["repo"], match["type"]) != (self.repo_id, self.type):
            raise ValueError(f"ID {self.id!r} does not match repo {self.repo_id} / {self.type}")
        return self

    def locations(self) -> list[Location]:
        """Every location in the ground truth (for validation)."""
        truth = self.ground_truth
        if isinstance(truth, LookupTruth):
            return [truth.definition]
        if isinstance(truth, AnswerSetTruth):
            return list(truth.answers)
        if isinstance(truth, ImpactTruth):
            return [truth.target, *truth.static]
        if isinstance(truth, FlowTruth):
            return list(truth.must_cite)
        return []
