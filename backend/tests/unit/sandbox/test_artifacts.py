"""SBX-10: every committed manifest lists each artifact with its sha256 and the recipe hash."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoatlas.sandbox.artifacts import MANIFEST_JSON, Manifest, sha256_file
from repoatlas.sandbox.paths import repo_root, sandbox_dir
from repoatlas.sandbox.recipe import load_recipe

RECIPES = sorted((sandbox_dir() / "recipes").glob("*.yaml"))
REQUIRED = {"image.json", "baseline.json", "scip.json", "index.scip"}


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: p.stem)
def test_manifest_matches_artifacts(path: Path) -> None:
    recipe = load_recipe(path)
    folder = repo_root() / "eval" / "artifacts" / recipe.id / recipe.commit
    manifest = Manifest.model_validate_json((folder / MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest.commit == recipe.commit
    assert manifest.recipe_sha256 == recipe.digest()
    listed = {entry.path: entry for entry in manifest.artifacts}
    assert set(listed) >= REQUIRED
    assert not listed["index.scip"].in_git
    for name, entry in listed.items():
        file = folder / name
        if entry.in_git:
            assert file.is_file(), name
        if file.is_file():  # index.scip exists only where it was built
            assert sha256_file(file) == entry.sha256, name
    on_disk = {p.name for p in folder.iterdir() if p.is_file() and p.name != MANIFEST_JSON}
    assert on_disk <= set(listed)
    assert {"repoatlas", "docker", "base_image", "repo_image", recipe.scip.indexer} <= set(
        manifest.tools
    )
