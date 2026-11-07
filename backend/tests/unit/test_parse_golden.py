"""Golden tests: parsing the fixture repositories must produce byte-identical JSON on every OS.

Regenerate after an intentional extractor change with:
    REPOATLAS_UPDATE_GOLDEN=1 uv run pytest tests/unit/test_parse_golden.py
then review the diff of tests/fixtures/golden/*.parse.json before committing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.ingest.source import DirectorySourceTree
from repoatlas.parsing.models import Range
from repoatlas.pipeline.parse import parse_tree

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPS = {"max_source_files": 100, "max_file_bytes": 100_000}


@pytest.mark.parametrize("name", ["py_small", "ts_small"])
def test_fixture_parse_matches_golden(name: str) -> None:
    with DirectorySourceTree(FIXTURES / "repos" / name, label=f"dir:{name}") as tree:
        output = parse_tree(tree, **CAPS)
    actual = pretty_json(output)
    golden = FIXTURES / "golden" / f"{name}.parse.json"
    if os.environ.get("REPOATLAS_UPDATE_GOLDEN") == "1":
        write_text_lf(golden, actual)
    assert golden.read_bytes().decode("utf-8") == actual, (
        f"{golden.name} differs; if the change is intended, regenerate it (see module docstring)"
    )


def _within(inner: Range, outer: Range) -> bool:
    start_ok = (outer.start_line, outer.start_col) <= (inner.start_line, inner.start_col)
    return start_ok and (inner.end_line, inner.end_col) <= (outer.end_line, outer.end_col)


@pytest.mark.parametrize("name", ["py_small", "ts_small"])
def test_fixture_ranges_point_at_the_code(name: str) -> None:
    """Name ranges cover the name (quotes included for string names); calls sit in their symbol."""
    root = FIXTURES / "repos" / name
    with DirectorySourceTree(root, label=f"dir:{name}") as tree:
        output = parse_tree(tree, **CAPS)
    for parsed in output.files:
        lines = (root / parsed.path).read_bytes().split(b"\n")
        spans = {sym.key: sym.span for sym in parsed.symbols}
        for sym in parsed.symbols:
            r = sym.name_range
            assert r.start_line == r.end_line, sym.key
            text = lines[r.start_line - 1][r.start_col : r.end_col].decode("utf-8")
            assert text in (sym.name, *(f"{q}{sym.name}{q}" for q in "\"'`")), (sym.key, text)
            assert _within(r, sym.span), sym.key
        for call in parsed.calls:
            if call.enclosing is not None:
                assert _within(call.range, spans[call.enclosing]), (call.callee, call.enclosing)


def test_fixture_digests_are_stable() -> None:
    digests = []
    for _ in range(2):
        with DirectorySourceTree(FIXTURES / "repos" / "py_small", label="dir:py_small") as tree:
            digests.append(parse_tree(tree, **CAPS).digest)
    assert digests[0] == digests[1]
