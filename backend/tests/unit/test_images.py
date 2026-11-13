"""The production images keep their promises (DEP-1): multi-stage, base images pinned by
digest, no root, and the embedding model inside the backend images."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.config import Settings, get_settings
from repoatlas.retrieval.embed import DEFAULT_MODEL, FastEmbedEmbedder, embedder_for

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
DOCKERFILES = sorted((INFRA / "docker").glob("*.Dockerfile"))
PINNED = re.compile(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")


def instructions(path: Path) -> list[tuple[str, str]]:
    """``(INSTRUCTION, arguments)`` for each instruction, continuation lines joined."""
    joined = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8"))
    found = []
    for line in joined.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, _, rest = line.partition(" ")
            found.append((name.upper(), rest.strip()))
    return found


def test_there_is_an_image_for_each_service() -> None:
    assert [path.name for path in DOCKERFILES] == [
        "api.Dockerfile",
        "web.Dockerfile",
        "worker.Dockerfile",
    ]


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda path: path.name)
def test_base_images_are_pinned_by_digest(dockerfile: Path) -> None:
    steps = instructions(dockerfile)
    images = {}
    for name, rest in steps:
        if name == "ARG" and rest.endswith("_IMAGE") is False and "_IMAGE=" in rest:
            key, _, value = rest.partition("=")
            images[key] = value
    assert images, "base images are declared as ARG <NAME>_IMAGE=<image>@sha256:…"
    for key, value in images.items():
        assert PINNED.fullmatch(value), f"{key} is not pinned by digest: {value}"
    stages = set()
    for name, rest in steps:
        if name != "FROM":
            continue
        source, _, stage = rest.partition(" AS ")
        # Every stage starts from a pinned image or from an earlier stage.
        assert source in stages or source.strip("${}") in images, rest
        stages.add(stage.strip())
    assert len(stages) >= 2, "the build is multi-stage"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda path: path.name)
def test_the_runtime_stage_does_not_run_as_root(dockerfile: Path) -> None:
    steps = instructions(dockerfile)
    last_from = max(index for index, (name, _) in enumerate(steps) if name == "FROM")
    users = [rest for name, rest in steps[last_from:] if name == "USER"]
    assert users and users[-1] not in {"root", "0", "0:0"}
    assert any(name == "CMD" for name, _ in steps[last_from:])


def test_the_backend_images_carry_the_embedding_model() -> None:
    for name in ("api.Dockerfile", "worker.Dockerfile"):
        text = (INFRA / "docker" / name).read_text(encoding="utf-8")
        assert "repoatlas models fetch" in text
        assert "COPY --from=model" in text and "REPOATLAS_MODEL_CACHE_DIR=/opt/models" in text
        assert f"ARG EMBEDDING_MODEL={DEFAULT_MODEL}" in text


def test_models_fetch_prepares_the_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOATLAS_EMBEDDING_MODEL", "hash-32")
    monkeypatch.setenv("REPOATLAS_MODEL_CACHE_DIR", str(tmp_path / "models"))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["models", "fetch"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "hash-32: 32 dimensions" in result.output and "models" in result.output


def test_the_model_cache_directory_is_a_setting(tmp_path: Path) -> None:
    def settings(**values: Any) -> Settings:
        return Settings(_env_file=None, embedding_model=DEFAULT_MODEL, data_dir=tmp_path, **values)

    default = embedder_for(settings())
    baked = embedder_for(settings(model_cache_dir=Path("/opt/models")))
    assert isinstance(default, FastEmbedEmbedder) and isinstance(baked, FastEmbedEmbedder)
    assert default._cache_dir == tmp_path / "models"
    assert baked._cache_dir == Path("/opt/models")
