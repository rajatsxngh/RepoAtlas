"""Per-repo recipes (SBX-3): what to fetch, how to install, how to test, how to index.

Recipes are YAML files under ``sandbox/recipes/``. ``load_recipe`` reports validation errors with
the line number of the offending value, so a typo in a recipe points at its line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from repoatlas.canonical import digest

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

Reporter = Literal["junit", "vitest-json", "jest-json"]
Indexer = Literal["scip-python", "scip-typescript"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TestSpec(_Model):
    __test__ = False  # not a pytest test class

    cmd: str = Field(min_length=1)
    reporter: Reporter
    # Where the reporter writes its file inside the container; must be under /tmp/out.
    report_path: str = "/tmp/out/report"
    timeout_s: int = Field(default=900, ge=10, le=7200)
    # How a re-run selects tests: pytest node IDs appended to ``cmd``, or test file paths.
    select: Literal["nodeids", "files"] | None = None

    @field_validator("report_path")
    @classmethod
    def _under_out(cls, value: str) -> str:
        if not value.startswith("/tmp/out/") or ".." in value:
            raise ValueError("report_path must be a file under /tmp/out/")
        return value

    @property
    def selection(self) -> Literal["nodeids", "files"]:
        if self.select is not None:
            return self.select
        return "nodeids" if self.reporter == "junit" else "files"


class ScipSpec(_Model):
    indexer: Indexer
    # The indexer must write /tmp/out/index.scip (pass it with --output).
    args: list[str] = []
    cwd: str = "."  # where the indexer runs, relative to the source root
    # "tmp": index a writable copy in /tmp/work (for setup steps that write files there).
    workdir: Literal["src", "tmp"] = "src"
    setup: list[str] = []  # shell commands run in the indexer's directory first
    timeout_s: int = Field(default=1800, ge=10, le=7200)

    @field_validator("cwd")
    @classmethod
    def _relative(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("cwd must be a relative path inside the source")
        return value


class Recipe(_Model):
    id: str
    url: str
    commit: str
    language: Literal["python", "typescript"]
    base_image: Literal["python", "node"]
    install: list[str] = Field(min_length=1)
    test: TestSpec
    scip: ScipSpec
    # "tmp": the suite writes to its working directory, so it runs from a copy in /tmp/work.
    workdir: Literal["src", "tmp"] = "src"
    env: dict[str, str] = {}
    # Test IDs excluded from ground truth: flaky across baseline runs, or failing in every run.
    quarantine: list[str] = []

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("id must be lowercase letters, digits, and dashes (at most 40)")
        return value

    @field_validator("url")
    @classmethod
    def _github_url(cls, value: str) -> str:
        if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", value):
            raise ValueError("url must be https://github.com/<owner>/<repo>")
        return value

    @field_validator("commit", mode="before")
    @classmethod
    def _pinned_commit(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("commit must be a quoted string (YAML reads some SHAs as numbers)")
        if not _SHA_RE.fullmatch(value):
            raise ValueError("commit must be a full 40-character lowercase hex SHA")
        return value

    @property
    def clone_url(self) -> str:
        return self.url + ".git"

    def digest(self) -> str:
        """sha256 of the recipe's canonical JSON (recorded in manifests)."""
        return digest(self)


class RecipeError(ValueError):
    """A recipe file that does not parse or validate; the message names file and line."""


def _line_map(text: str) -> dict[tuple[str | int, ...], int]:
    """1-based line of every value in the YAML document, keyed by its path."""
    lines: dict[tuple[str | int, ...], int] = {}

    def walk(node: yaml.Node, path: tuple[str | int, ...]) -> None:
        lines[path] = node.start_mark.line + 1
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                key = str(key_node.value)
                walk(value_node, (*path, key))
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, (*path, index))

    root = yaml.compose(text, Loader=yaml.SafeLoader)
    if root is not None:
        walk(root, ())
    return lines


def _error_line(lines: dict[tuple[str | int, ...], int], loc: tuple[str | int, ...]) -> int:
    for end in range(len(loc), -1, -1):
        if loc[:end] in lines:
            return lines[loc[:end]]
    return 1


def parse_recipe(text: str, *, source: str = "<recipe>") -> Recipe:
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else 1
        raise RecipeError(f"{source}:{line}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeError(f"{source}:1: a recipe must be a YAML mapping")
    try:
        return Recipe.model_validate(data)
    except ValidationError as exc:
        lines = _line_map(text)
        messages = []
        for error in exc.errors():
            loc = tuple(error["loc"])
            line = _error_line(lines, loc)
            where = ".".join(str(part) for part in loc) or "<root>"
            messages.append(f"{source}:{line}: {where}: {error['msg']}")
        raise RecipeError("\n".join(messages)) from exc


def load_recipe(path: Path) -> Recipe:
    return parse_recipe(path.read_text(encoding="utf-8"), source=str(path))


def write_quarantine(path: Path, quarantine: list[str]) -> None:
    """Replace the recipe's ``quarantine`` list in place, keeping the rest of the file."""
    text = path.read_text(encoding="utf-8")
    block_re = re.compile(r"^quarantine:.*(?:\n(?:[ \t]+\S.*|-.*))*", re.MULTILINE)
    items = sorted(set(quarantine))
    if items:
        # JSON strings are valid YAML double-quoted scalars.
        rendered = "quarantine:\n" + "".join(f"  - {json.dumps(item)}\n" for item in items)
    else:
        rendered = "quarantine: []\n"
    if block_re.search(text):
        new_text = block_re.sub(lambda _m: rendered.rstrip("\n"), text, count=1)
    else:
        new_text = text.rstrip("\n") + "\n" + rendered
    if not new_text.endswith("\n"):
        new_text += "\n"
    parse_recipe(new_text, source=str(path))  # never write a recipe that no longer loads
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(new_text)
