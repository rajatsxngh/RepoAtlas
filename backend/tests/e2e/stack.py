"""Serve the API and a worker over the fixture repositories in ``tests/e2e/fixtures.py``.

    cd backend
    uv run python -m tests.e2e.stack --port 8765 --web-origin http://127.0.0.1:3100

Builds go through the real pipeline (fetch, inventory, parse, graph, store) from local bare git
repositories; GitHub is never contacted. The database is the configured one's name plus
``_e2e`` on the same server, dropped and created again at start, so runs never touch real data.
Stop with Ctrl+C (the web app's Playwright config starts and stops it).
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import threading
from pathlib import Path

import uvicorn
from sqlalchemy import create_engine, make_url, text

from repoatlas.api.app import create_app
from repoatlas.config import Settings, get_settings
from repoatlas.db import migrate
from repoatlas.ingest.github import GitHubClient
from repoatlas.jobs.worker import Worker
from tests.e2e.fixtures import build_repos, github_transport, patch_fetches


def fresh_database(base_url: str) -> str:
    """Drop and create ``<database>_e2e`` next to ``base_url``; return its URL, migrated."""
    admin_url = make_url(base_url)
    name = f"{admin_url.database or 'repoatlas'}_e2e"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    url = admin_url.set(database=name).render_as_string(hide_password=False)
    migrate.upgrade(url)
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--web-origin",
        action="append",
        default=[],
        help="An origin the web app is served from (repeatable).",
    )
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="repoatlas-e2e-"))
    repos = build_repos(work / "origins")
    transport = github_transport(repos)
    GitHubClient.from_settings = classmethod(  # type: ignore[method-assign, assignment]
        lambda cls, _settings: cls(transport=transport)
    )
    patch_fetches(repos, setattr)

    origins = args.web_origin or ["http://127.0.0.1:3100", "http://localhost:3100"]
    settings = Settings(
        _env_file=None,
        database_url=fresh_database(get_settings().database_url),
        data_dir=work / "data",
        web_origins=",".join(origins),
        rate_limit_enabled=False,
        worker_poll_s=0.2,
        job_retry_backoff_s=(1.0,),
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    worker = Worker(settings, engine=engine, worker_id="e2e")
    thread = threading.Thread(target=worker.run, name="e2e-worker", daemon=True)
    thread.start()
    try:
        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    finally:
        worker.request_stop()
        thread.join(timeout=10)
        engine.dispose()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
