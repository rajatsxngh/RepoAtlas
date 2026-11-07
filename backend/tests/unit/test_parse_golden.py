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


def test_fixture_digests_are_stable() -> None:
    digests = []
    for _ in range(2):
        with DirectorySourceTree(FIXTURES / "repos" / "py_small", label="dir:py_small") as tree:
            digests.append(parse_tree(tree, **CAPS).digest)
    assert digests[0] == digests[1]
