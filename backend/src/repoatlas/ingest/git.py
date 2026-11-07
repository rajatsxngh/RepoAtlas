"""Hermetic git invocations.

Machine-level git settings (system and global config) are ignored so they can't rewrite bytes
(``core.autocrlf``), open credential windows, or prompt for input. Only the https and file
transports are allowed. See decision 0002.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from repoatlas.ingest.errors import FetchError

GIT_CONFIG_ARGS: tuple[str, ...] = (
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.symlinks=false",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.version=2",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
    "-c",
    "protocol.file.allow=always",
)

_ENV_OVERRIDES = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_LFS_SKIP_SMUDGE": "1",
}


def git_command(args: Sequence[str]) -> list[str]:
    """Return the full argument list for a hermetic git invocation."""
    return ["git", *GIT_CONFIG_ARGS, *args]


def git_env() -> dict[str, str]:
    return {**os.environ, **_ENV_OVERRIDES}


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 300.0,
    input: bytes | None = None,
) -> bytes:
    """Run git and return stdout; failures raise ``FetchError`` with a short, safe reason."""
    try:
        proc = subprocess.run(
            git_command(args),
            cwd=cwd,
            env=git_env(),
            input=input,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FetchError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git {args[0]} timed out after {timeout:.0f} s.") from exc
    if proc.returncode != 0:
        raise FetchError(f"git {args[0]} failed: {_last_line(proc.stderr)}")
    return proc.stdout


def _last_line(stderr: bytes) -> str:
    text_lines = stderr.decode("utf-8", "replace").splitlines()
    lines = [line.strip() for line in text_lines if line.strip()]
    text = lines[-1] if lines else "no error output"
    return text if len(text) <= 300 else text[:299] + "…"
