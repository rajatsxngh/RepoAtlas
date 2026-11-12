from __future__ import annotations

import pytest

from repoatlas.bench.facts import Facts, build_facts
from repoatlas.bench.repos import RepoFiles
from repoatlas.bench.scip import ScipIndex
from repoatlas.ingest.source import DirectorySourceTree
from repoatlas.sandbox.parsed import parse_tree
from tests.unit.bench.fixture_indexes import FIXTURES

MINIPY_COMMIT = "1" * 40
MINITS_COMMIT = "2" * 40


def fixture_facts(name: str, repo_id: str, commit: str) -> tuple[Facts, RepoFiles]:
    root = FIXTURES / name
    files = RepoFiles(repo_id, commit, DirectorySourceTree(root))
    index = ScipIndex.load(root / "index.scip")
    return build_facts(index, parse_tree(files.tree), files), files


@pytest.fixture(scope="session")
def minipy() -> Facts:
    return fixture_facts("minipy", "minipy", MINIPY_COMMIT)[0]


@pytest.fixture(scope="session")
def mints() -> Facts:
    return fixture_facts("mints", "mints", MINITS_COMMIT)[0]
