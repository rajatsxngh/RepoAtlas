"""The security checklist can't drift from the suite (GRD-8): every item of
``docs/security-checklist.md`` names at least one test, and every test it names exists."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
CHECKLIST = ROOT / "docs" / "security-checklist.md"
TEXT = CHECKLIST.read_text(encoding="utf-8")

_ITEM = re.compile(r"^## (S\d+) — (.+)$", re.MULTILINE)
_PYTEST = re.compile(r"^- `(backend/tests/[\w/]+\.py)::(test_\w+)`$", re.MULTILINE)
_WEB = re.compile(r"^- `(frontend/[\w/.\-]+\.tsx?) :: \"(.+)\"`$", re.MULTILINE)
# The threats the design asks for, each of which must be an item (by a word of its title).
REQUIRED = {
    "Prompt injection": "S1",
    "XSS through repository text": "S2",
    "XSS through answer markdown": "S3",
    "Path traversal": "S4",
    "SSRF": "S5",
    "Oversized inputs": "S6",
    "Secrets": "S7",
}


def items() -> dict[str, tuple[str, str]]:
    """Item ID → (title, the item's text up to the next item)."""
    found = list(_ITEM.finditer(TEXT))
    sections = {}
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(TEXT)
        sections[match[1]] = (match[2], TEXT[match.end() : end])
    return sections


ITEMS = items()
PYTEST_REFS = sorted(set(_PYTEST.findall(TEXT)))
WEB_REFS = sorted(set(_WEB.findall(TEXT)))


def test_the_checklist_has_every_required_item_in_order() -> None:
    assert list(ITEMS) == [f"S{number}" for number in range(1, len(ITEMS) + 1)]
    assert len(ITEMS) >= 8
    for words, item in REQUIRED.items():
        assert ITEMS[item][0].startswith(words), (item, ITEMS[item][0])


@pytest.mark.parametrize("item", list(ITEMS))
def test_every_item_names_a_test(item: str) -> None:
    title, body = ITEMS[item]
    references = _PYTEST.findall(body) + _WEB.findall(body)
    assert references, f"{item} ({title}) has no test"
    # Every list line of the item is a well-formed reference (nothing half-written).
    lines = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(lines) == len(references), f"{item}: a list line is not a test reference"


@pytest.mark.parametrize(("path", "name"), PYTEST_REFS, ids=[f"{p}::{n}" for p, n in PYTEST_REFS])
def test_every_named_pytest_test_exists(path: str, name: str) -> None:
    file = ROOT / path
    assert file.is_file(), f"{path} does not exist"
    source = file.read_text(encoding="utf-8")
    assert re.search(rf"^(?:async )?def {re.escape(name)}\(", source, re.MULTILINE), (
        f"{path} has no test named {name}"
    )
    # Not skipped or expected to fail: a checklist test has to be able to fail.
    before = source[: source.index(f"def {name}(")].rsplit("\n\n", 1)[-1]
    assert "skip" not in before and "xfail" not in before


@pytest.mark.parametrize(("path", "title"), WEB_REFS, ids=[f"{p}::{t[:40]}" for p, t in WEB_REFS])
def test_every_named_web_test_exists(path: str, title: str) -> None:
    file = ROOT / path
    assert file.is_file(), f"{path} does not exist"
    source = file.read_text(encoding="utf-8")
    assert re.search(rf"\b(?:it|test)\(\s*\"{re.escape(title)}\"", source), (
        f"{path} has no test titled {title!r}"
    )
    assert f'.skip("{title}"' not in source and f'.fixme("{title}"' not in source


def test_the_checklist_says_what_is_not_yet_shown() -> None:
    injection = ITEMS["S1"][1]
    assert "Not yet shown" in injection and "live-pass.md" in injection
    assert len(PYTEST_REFS) >= 40 and len(WEB_REFS) >= 8
