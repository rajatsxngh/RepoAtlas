"""The gallery list file (LCH-2): which repositories to precompute, at which commits.

``load_gallery_list`` reads ``launch/repos.yaml`` and rejects a list the gallery should not
show: a URL that is not a GitHub repository, a commit that is not a full SHA (a branch name
would move), a repository listed twice, a license outside the permissive ones, or text that is
too long for a card. Titles and descriptions are written in the list by its authors; nothing
on a gallery card comes from repository content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from repoatlas.ingest.errors import InvalidRepoUrl
from repoatlas.ingest.github import RepoRef, parse_repo_url

__all__ = [
    "MAX_ENTRIES",
    "PERMISSIVE_LICENSES",
    "GalleryEntry",
    "GalleryList",
    "GalleryListError",
    "load_gallery_list",
    "parse_gallery_list",
]

# SPDX identifiers of licenses that allow showing the code with attribution.
PERMISSIVE_LICENSES = frozenset(
    {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "0BSD", "Unlicense"}
)
MAX_ENTRIES = 24
_FULL_SHA = re.compile(r"[0-9a-f]{40}")

CardText = Annotated[str, Field(min_length=1, max_length=120)]


class GalleryListError(ValueError):
    """The list file can't be used; the message says what to fix."""


class GalleryEntry(BaseModel):
    """One repository of the gallery."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    commit: str = Field(description="The full SHA of the pinned commit.")
    title: Annotated[str, Field(min_length=1, max_length=40)]
    description: CardText
    language: Literal["Python", "TypeScript"]
    license: str = Field(description="SPDX identifier.")
    benchmark: bool = Field(default=False, description="One of the benchmark's repositories.")

    @field_validator("url")
    @classmethod
    def _github_url(cls, value: str) -> str:
        try:
            ref = parse_repo_url(value)
        except InvalidRepoUrl as error:
            raise ValueError(str(error)) from None
        return f"https://github.com/{ref.full_name}"

    @field_validator("commit")
    @classmethod
    def _full_sha(cls, value: str) -> str:
        if not _FULL_SHA.fullmatch(value):
            raise ValueError("pin a full 40-character lowercase commit SHA, not a branch or tag")
        return value

    @field_validator("license")
    @classmethod
    def _permissive(cls, value: str) -> str:
        if value not in PERMISSIVE_LICENSES:
            allowed = ", ".join(sorted(PERMISSIVE_LICENSES))
            raise ValueError(f"{value!r} is not one of the permissive licenses ({allowed})")
        return value

    @field_validator("title", "description")
    @classmethod
    def _one_line(cls, value: str) -> str:
        text = value.strip()
        if not text or any(ord(char) < 32 for char in text):
            raise ValueError("must be one line of text")
        return text

    @property
    def ref(self) -> RepoRef:
        return parse_repo_url(self.url)


class GalleryList(BaseModel):
    """The list file: a version and the repositories, in card order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    repos: Annotated[tuple[GalleryEntry, ...], Field(min_length=1, max_length=MAX_ENTRIES)]

    @model_validator(mode="after")
    def _no_duplicates(self) -> GalleryList:
        seen: set[str] = set()
        for entry in self.repos:
            key = entry.ref.key
            if key in seen:
                raise ValueError(f"{entry.ref.full_name} is listed twice")
            seen.add(key)
        return self


def parse_gallery_list(text: str, *, source: str = "the gallery list") -> GalleryList:
    """Parse and check the YAML ``text`` of a list file."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise GalleryListError(f"{source} is not valid YAML: {error}") from None
    if not isinstance(data, dict):
        raise GalleryListError(f"{source} must be a mapping with `version` and `repos`.")
    try:
        return GalleryList.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'list'}: {issue['msg']}"
            for issue in error.errors()
        )
        raise GalleryListError(f"{source} is not usable: {problems}") from None


def load_gallery_list(path: Path) -> GalleryList:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GalleryListError(f"Can't read {path}: {error.strerror or error}") from None
    return parse_gallery_list(text, source=str(path))
