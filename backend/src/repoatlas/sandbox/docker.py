"""A thin wrapper over the Docker CLI (subprocess; no Docker SDK, no socket access from Python)."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Docker Desktop on Windows installs the CLI here without always putting it on PATH.
_WINDOWS_DOCKER = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")


class DockerError(RuntimeError):
    """A Docker command failed; the message carries the command's last stderr lines."""


@dataclass(frozen=True, slots=True)
class DockerResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace").strip()


@lru_cache(maxsize=1)
def docker_bin() -> str:
    """The docker executable: ``REPOATLAS_DOCKER_BIN``, then PATH, then Docker Desktop's folder."""
    configured = os.environ.get("REPOATLAS_DOCKER_BIN")
    if configured:
        return configured
    found = shutil.which("docker")
    if found:
        return found
    if _WINDOWS_DOCKER.is_file():
        return str(_WINDOWS_DOCKER)
    raise DockerError("The Docker CLI was not found; set REPOATLAS_DOCKER_BIN.")


def _tail(data: bytes, lines: int = 5) -> str:
    text = data.decode("utf-8", "replace").strip().splitlines()
    return "\n".join(text[-lines:]) or "no error output"


def docker(
    args: Sequence[str],
    *,
    input: bytes | None = None,
    timeout: float | None = 600.0,
    check: bool = True,
) -> DockerResult:
    """Run ``docker <args>``; with ``check``, a non-zero exit raises ``DockerError``."""
    try:
        proc = subprocess.run(
            [docker_bin(), *args],
            input=input,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"docker {args[0]} timed out after {timeout} s") from exc
    result = DockerResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise DockerError(f"docker {' '.join(args[:2])} failed: {_tail(proc.stderr)}")
    return result


def docker_available() -> bool:
    """True when the CLI exists and the daemon answers."""
    try:
        return (
            docker(
                ["version", "--format", "{{.Server.Version}}"], timeout=20, check=False
            ).returncode
            == 0
        )
    except (DockerError, OSError):
        return False


def image_id(ref: str) -> str | None:
    """The local image ID (``sha256:…``) for ``ref``, or ``None`` when it is not present."""
    result = docker(["image", "inspect", "--format", "{{.Id}}", ref], check=False, timeout=60)
    return result.text if result.returncode == 0 else None


def remove_container(name: str) -> None:
    docker(["rm", "-f", name], check=False, timeout=60)


def remove_image(ref: str) -> None:
    docker(["image", "rm", "-f", ref], check=False, timeout=120)
