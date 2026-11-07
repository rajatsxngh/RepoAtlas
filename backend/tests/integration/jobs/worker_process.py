"""A worker process for the kill test: it builds from a local origin and hangs in the graph stage.

    python -m tests.integration.jobs.worker_process <origin url> <marker file>

Settings come from ``REPOATLAS_*`` environment variables. When the build reaches the graph
stage (so fetch, inventory, and parse have saved their checkpoints), the process writes the
marker file and waits to be killed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from repoatlas.config import get_settings
from repoatlas.ingest import fetch as fetch_module
from repoatlas.jobs.worker import Worker
from repoatlas.pipeline import build as build_module


def main(origin_url: str, marker: Path) -> None:
    real_source, real_history = fetch_module.fetch_source, fetch_module.fetch_history

    def source(_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        return real_source(origin_url, commit, dest, **kwargs)

    def history(_url: str, commit: str, dest: Path, **kwargs: Any) -> Any:
        return real_history(origin_url, commit, dest, **kwargs)

    def hang(*_args: Any, **_kwargs: Any) -> Any:
        marker.write_text("in the graph stage\n", encoding="utf-8")
        while True:
            time.sleep(1)

    for name, replacement in (
        ("fetch_source", source),
        ("fetch_history", history),
        ("build_graph", hang),
    ):
        setattr(build_module, name, replacement)
    Worker(get_settings(), worker_id="victim").run()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
