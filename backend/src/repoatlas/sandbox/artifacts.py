"""Ground-truth artifacts under ``eval/artifacts/<repo_id>/<commit>/`` (SBX-10)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.sandbox.paths import artifacts_dir
from repoatlas.sandbox.recipe import Recipe

IMAGE_JSON = "image.json"
BASELINE_JSON = "baseline.json"
SCIP_INDEX = "index.scip"
SCIP_JSON = "scip.json"
MUTATIONS_JSONL = "mutations.jsonl"
MANIFEST_JSON = "manifest.json"


def recipe_dir(recipe: Recipe) -> Path:
    return artifacts_dir(recipe.id, recipe.commit)


def write_model(recipe: Recipe, name: str, model: BaseModel) -> Path:
    path = recipe_dir(recipe) / name
    write_text_lf(path, pretty_json(model))
    return path


def read_model[M: BaseModel](recipe: Recipe, name: str, model: type[M]) -> M | None:
    path = recipe_dir(recipe) / name
    if not path.is_file():
        return None
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
