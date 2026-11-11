"""SBX-2 and SBX-5: repo images bake the source in, install through the proxy, and are pinned."""

from __future__ import annotations

import json

from repoatlas.sandbox.docker import docker
from repoatlas.sandbox.images import ensure_base_image
from tests.integration.sandbox.fixtures import minipy_image


def test_base_image_runs_as_the_sandbox_user() -> None:
    base = ensure_base_image("python")
    assert base.digest.startswith("sha256:")
    assert all("@sha256:" in ref for ref in base.upstream)
    result = docker(["run", "--rm", "--network", "none", base.tag, "id", "-u"])
    assert result.text == "10001"


def test_repo_image_has_the_source_and_installed_dependencies() -> None:
    info = minipy_image()
    assert info.image.digest.startswith("sha256:")
    assert info.files == 4
    script = (
        "stat -c '%u %a %n' /src/mini/calc.py"
        " && python -c 'import pytest; print(pytest.__version__)'"
    )
    result = docker(["run", "--rm", "--network", "none", info.image.tag, "sh", "-c", script])
    owner_line, version = result.text.splitlines()
    assert owner_line == "10001 644 /src/mini/calc.py"
    assert version == "8.3.3"


def test_repo_image_config_has_no_proxy_settings() -> None:
    info = minipy_image()
    config = json.loads(
        docker(["image", "inspect", "--format", "{{json .Config}}", info.image.tag]).text
    )
    env = " ".join(config["Env"] or [])
    assert "PROXY" not in env.upper()
    assert config["User"] == "10001:10001"
