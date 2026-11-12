"""Ground-truth artifacts under ``eval/artifacts/<repo_id>/<commit>/`` (SBX-10)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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


# --------------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------------

LOCAL_ONLY_SUFFIXES = (".scip",)  # large; gitignored, reproducible from the recorded images


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str
    size: int
    in_git: bool


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_id: str
    url: str
    commit: str
    recipe_sha256: str
    artifacts: list[ManifestEntry]
    tools: dict[str, str]


def build_manifest(recipe: Recipe, tools: dict[str, str]) -> Manifest:
    folder = recipe_dir(recipe)
    entries = [
        ManifestEntry(
            path=path.name,
            sha256=sha256_file(path),
            size=path.stat().st_size,
            in_git=not path.name.endswith(LOCAL_ONLY_SUFFIXES),
        )
        for path in sorted(folder.iterdir())
        if path.is_file() and path.name != MANIFEST_JSON
    ]
    return Manifest(
        repo_id=recipe.id,
        url=recipe.url,
        commit=recipe.commit,
        recipe_sha256=recipe.digest(),
        artifacts=entries,
        tools=dict(sorted(tools.items())),
    )


def write_manifest(recipe: Recipe, tools: dict[str, str]) -> Path:
    return write_model(recipe, MANIFEST_JSON, build_manifest(recipe, tools))
