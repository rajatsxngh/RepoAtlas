"""Importing LiteLLM through RepoAtlas never loads .env into the environment or fetches config
from the network."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = """
import os, socket
attempts = []
real = socket.getaddrinfo
def guard(host, *args, **kwargs):
    attempts.append(host)
    raise OSError("blocked")
socket.getaddrinfo = guard
import repoatlas.llm.transport as transport
transport._litellm()
import litellm.anthropic_interface  # what the transport calls
print("leaked" if "REPOATLAS_CANARY" in os.environ else "clean", sorted(set(attempts)))
"""


def test_litellm_neither_loads_dotenv_nor_fetches(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("REPOATLAS_CANARY=secret\n", encoding="utf-8")
    env = {
        key: value
        for key, value in __import__("os").environ.items()
        if not key.startswith(("LITELLM_", "REPOATLAS_"))
    }
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "clean []", result.stdout + result.stderr
