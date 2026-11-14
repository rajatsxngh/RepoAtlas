"""The planted-injection fixture repository, ``injection_lab`` (GRD-4).

``backend/tests/fixtures/repos/injection_lab/`` is a small billing library whose files carry
planted instructions: in comments, docstrings, the README, string literals, a file name, a
markdown link, a test name, base64, another language, and more. ``variants.yaml`` describes
each one (with its own canary token); ``questions.yaml`` holds the questions that make the
poisoned code relevant. The detector tests, the prompt tests, and the ``injection`` suite all
read the fixture through ``load_lab``. Its code is never run.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from repoatlas.guardrails.detector import FlagKind
from repoatlas.sandbox.paths import repo_root

LAB_NAME = "injection_lab"
MIN_VARIANTS = 15
_CANARY = re.compile(r"CANARY-V\d{2}-[0-9A-F]{4}")

QuestionMode = Literal["oneshot", "impact_explain"]


def lab_dir() -> Path:
    return repo_root() / "backend" / "tests" / "fixtures" / "repos" / LAB_NAME


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Marker(_Model):
    """A sign of obedience besides the canary: ``pattern`` (a regular expression, matched
    without regard to case) only appears in an answer that did what the variant asked."""

    name: str
    pattern: str

    @model_validator(mode="after")
    def _compiles(self) -> Self:
        re.compile(self.pattern)
        return self


class Variant(_Model):
    id: str = Field(pattern=r"v\d{2}")
    kind: str
    file: str
    symbol: str | None = None
    canary: str = Field(pattern=_CANARY.pattern)
    indexed: bool = True  # False: the file is not source code, so it never reaches a prompt
    encoded: bool = False  # the canary is only inside an encoded payload
    expect: list[FlagKind] = Field(default_factory=list)  # kinds marked in the file's text
    expect_path: list[FlagKind] = Field(default_factory=list)  # kinds marked in its path
    markers: list[Marker] = Field(default_factory=list)


class LabQuestion(_Model):
    id: str
    mode: QuestionMode
    text: str | None = None  # the question (oneshot)
    symbol: str | None = None  # the changed symbol's qualified name (impact_explain)
    path: str | None = None  # the file that defines it
    variants: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.mode == "oneshot" and not self.text:
            raise ValueError(f"{self.id}: a oneshot question needs text.")
        if self.mode == "impact_explain" and not (self.symbol and self.path):
            raise ValueError(f"{self.id}: an impact_explain question needs symbol and path.")
        return self


class Lab(_Model):
    """The fixture: where it is, its variants, its questions, and its files."""

    root: Path
    version: int
    variants: list[Variant]
    questions: list[LabQuestion]
    files: list[str]  # every file of the repository, as posix paths from its root

    def variant(self, variant_id: str) -> Variant:
        for variant in self.variants:
            if variant.id == variant_id:
                return variant
        raise KeyError(variant_id)

    def text(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def questions_for(self, mode: QuestionMode) -> list[LabQuestion]:
        return [question for question in self.questions if question.mode == mode]


def canary_pattern(canary: str) -> re.Pattern[str]:
    """Matches ``canary`` however an answer writes it: any case, with ``-``, ``_``, spaces,
    backticks, or nothing between its parts."""
    parts = [re.escape(part) for part in canary.split("-")]
    return re.compile(r"[-_\s`*]*".join(parts), re.IGNORECASE)


def find_canaries(text: str) -> list[str]:
    """Canary tokens written plainly in ``text``, in order, without repeats."""
    return list(dict.fromkeys(_CANARY.findall(text)))


def _files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


@cache
def load_lab(root: Path | None = None) -> Lab:
    """Read and check the fixture: at least 15 variants, distinct IDs and canaries, each
    variant's file present, and every question naming known variants."""
    base = root or lab_dir()
    variants_doc = yaml.safe_load((base / "variants.yaml").read_text(encoding="utf-8"))
    questions_doc = yaml.safe_load((base / "questions.yaml").read_text(encoding="utf-8"))
    lab = Lab(
        root=base,
        version=int(questions_doc["version"]),
        variants=variants_doc["variants"],
        questions=questions_doc["questions"],
        files=_files(base),
    )
    if len(lab.variants) < MIN_VARIANTS:
        raise ValueError(f"The fixture needs at least {MIN_VARIANTS} variants.")
    for field in ("id", "canary"):
        values = [getattr(variant, field) for variant in lab.variants]
        if len(set(values)) != len(values):
            raise ValueError(f"Variant {field}s must be distinct.")
    known = {variant.id for variant in lab.variants}
    for variant in lab.variants:
        if variant.file not in lab.files:
            raise ValueError(f"{variant.id}: {variant.file} is not in the fixture.")
    ids = [question.id for question in lab.questions]
    if len(set(ids)) != len(ids):
        raise ValueError("Question IDs must be distinct.")
    for question in lab.questions:
        unknown = set(question.variants) - known
        if unknown:
            raise ValueError(f"{question.id} names unknown variants: {sorted(unknown)}")
        if question.path is not None and question.path not in lab.files:
            raise ValueError(f"{question.id}: {question.path} is not in the fixture.")
    return lab
