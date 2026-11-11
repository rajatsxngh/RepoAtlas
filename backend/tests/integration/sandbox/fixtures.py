"""Recipes and images for the fixture projects under ``tests/fixtures/sandbox``."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from repoatlas.ingest.source import DirectorySourceTree
from repoatlas.sandbox.images import RepoImageInfo, build_image
from repoatlas.sandbox.recipe import Recipe, parse_recipe

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sandbox"

MINIPY_RECIPE = """\
id: minipy
url: https://github.com/example/minipy
commit: "0000000000000000000000000000000000000001"
language: python
base_image: python
install: ["uv pip install -r requirements.txt"]
test:
  cmd: "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml=/tmp/out/report"
  reporter: junit
  timeout_s: 120
scip:
  indexer: scip-python
  args: ["index", ".", "--project-name", "minipy", "--project-version", "0"]
"""


def minipy_recipe() -> Recipe:
    return parse_recipe(MINIPY_RECIPE)


@cache
def minipy_image() -> RepoImageInfo:
    """Built once per test session (Docker's cache makes rebuilds fast)."""
    return build_image(minipy_recipe(), tree=DirectorySourceTree(FIXTURES / "minipy"))
