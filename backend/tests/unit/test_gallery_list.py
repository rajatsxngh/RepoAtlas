"""The gallery list file (LCH-2): the committed list is usable, and unusable lists are refused
with a message that says what to fix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from repoatlas.gallery.listing import (
    PERMISSIVE_LICENSES,
    GalleryListError,
    load_gallery_list,
    parse_gallery_list,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
LIST = ROOT / "launch" / "repos.yaml"
SHA = "a" * 40


def entry(**changes: Any) -> dict[str, Any]:
    base = {
        "url": "https://github.com/pallets/flask",
        "commit": SHA,
        "title": "Flask",
        "description": "A small web framework.",
        "language": "Python",
        "license": "BSD-3-Clause",
    }
    return {**base, **changes}


def listing(*entries: dict[str, Any]) -> str:
    return yaml.safe_dump({"version": 1, "repos": list(entries)})


def test_the_committed_list_has_ten_pinned_permissive_repos() -> None:
    found = load_gallery_list(LIST)

    assert len(found.repos) == 10
    assert len({item.ref.key for item in found.repos}) == 10
    assert all(item.license in PERMISSIVE_LICENSES for item in found.repos)
    languages = {item.language for item in found.repos}
    assert languages == {"Python", "TypeScript"}  # a mix (design notes)


def test_the_committed_list_holds_the_benchmark_repos_at_the_benchmark_commits() -> None:
    """The published numbers are about code a visitor can open."""
    commits = set()
    with (ROOT / "eval" / "benchmark" / "questions.jsonl").open(encoding="utf-8") as lines:
        for line in lines:
            commits.add(json.loads(line)["commit"])
    marked = {item.commit for item in load_gallery_list(LIST).repos if item.benchmark}

    assert len(commits) == 5
    assert marked == commits


def test_a_url_is_normalized_and_an_entry_knows_its_repo() -> None:
    found = parse_gallery_list(listing(entry(url="https://github.com/pallets/flask.git/")))

    assert found.repos[0].url == "https://github.com/pallets/flask"
    assert found.repos[0].ref.full_name == "pallets/flask"
    assert found.repos[0].benchmark is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"commit": "main"}, "full 40-character"),
        ({"commit": SHA[:12]}, "full 40-character"),
        ({"commit": SHA.upper()}, "full 40-character"),
        ({"url": "https://gitlab.com/pallets/flask"}, "github.com"),
        ({"url": "http://github.com/pallets/flask"}, "https://"),
        ({"license": "GPL-3.0-only"}, "permissive"),
        ({"license": "NOASSERTION"}, "permissive"),
        ({"language": "Rust"}, "language"),
        ({"title": "  "}, "one line"),
        ({"description": "two\nlines"}, "one line"),
        ({"description": "x" * 121}, "description"),
        ({"stars": 5}, "stars"),
    ],
)
def test_an_unusable_entry_is_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(GalleryListError, match=message):
        parse_gallery_list(listing(entry(**changes)))


def test_a_repo_listed_twice_is_refused_whatever_its_case() -> None:
    twice = listing(entry(), entry(url="https://github.com/Pallets/Flask", commit="b" * 40))

    with pytest.raises(GalleryListError, match="listed twice"):
        parse_gallery_list(twice)


@pytest.mark.parametrize(
    "text",
    ["", "- just\n- a list\n", "version: 2\nrepos: []\n", "version: 1\nrepos: []\n", ": :\n  - ["],
)
def test_a_file_that_is_not_a_list_is_refused(text: str) -> None:
    with pytest.raises(GalleryListError):
        parse_gallery_list(text)


def test_a_missing_file_says_which(tmp_path: Path) -> None:
    with pytest.raises(GalleryListError, match=r"missing.yaml"):
        load_gallery_list(tmp_path / "missing.yaml")
