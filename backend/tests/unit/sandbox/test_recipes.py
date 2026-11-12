"""The committed benchmark recipes (SBX-3, SBX-4)."""

from __future__ import annotations

from collections import Counter

import pytest

from repoatlas.sandbox.paths import sandbox_dir
from repoatlas.sandbox.recipe import Recipe, load_recipe

RECIPES = sorted((sandbox_dir() / "recipes").glob("*.yaml"))


def _load_all() -> list[Recipe]:
    return [load_recipe(path) for path in RECIPES]


def test_five_benchmark_repos_with_two_per_language() -> None:
    recipes = _load_all()
    assert len(recipes) == 5
    languages = Counter(recipe.language for recipe in recipes)
    assert languages["python"] >= 2 and languages["typescript"] >= 2


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: p.stem)
def test_recipe_is_pinned(path) -> None:
    recipe = load_recipe(path)
    assert recipe.id == path.stem
    assert len(recipe.commit) == 40
    assert recipe.base_image == ("python" if recipe.language == "python" else "node")
    for step in recipe.install:
        # A lockfile, or resolution limited to a date: never a floating install.
        pinned = any(
            marker in step
            for marker in ("--locked", "--exclude-newer", "npm ci", "--frozen-lockfile", "@")
        )
        assert pinned or "ln -sfn" in step, step
    assert recipe.test.report_path in recipe.test.cmd
    assert "/tmp/out/index.scip" in recipe.scip.args
