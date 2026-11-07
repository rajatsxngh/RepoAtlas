"""Response models: what clients see of recorded failures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repoatlas.api.schemas import JobView, public_error
from repoatlas.jobs.queue import Job

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "error",
    [
        None,
        "FetchError: git fetch failed: fatal: couldn't find remote ref",
        "RepoTooLarge: acme/huge has 9,000 source files; the limit is 5,000.",
        "PipelineMismatch: The build was requested for pipeline 5b75b751cfff, but …",
        "The worker stopped responding during attempt 2 (its lease ran out).",
        "The attempt ran longer than the 3600 s limit.",
    ],
)
def test_errors_written_for_people_are_shown_as_they_are(error: str | None) -> None:
    assert public_error(error) == error


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (
            "OperationalError: (psycopg.OperationalError) connection to server at "
            '"db.internal" (10.0.0.5), port 5432 failed [SQL: INSERT INTO blobs …]',
            "OperationalError",
        ),
        (r"RuntimeError: graph exploded at C:\Users\someone\secret.py", "RuntimeError"),
        ("KeyError: 'token'", "KeyError"),
    ],
)
def test_other_errors_show_only_their_type(error: str, kind: str) -> None:
    shown = public_error(error)
    assert shown is not None and shown.startswith(f"{kind}: the build failed")
    for secret in ("db.internal", "10.0.0.5", "INSERT", "someone", "token"):
        assert secret not in shown


def test_job_views_hide_payloads_and_internal_errors() -> None:
    job = Job(
        id=7,
        kind="build",
        payload={"snapshot_id": 3, "url": "https://github.com/a/b"},
        status="queued",
        stage="parse",
        progress=0.4,
        attempts=1,
        max_attempts=3,
        snapshot_id=3,
        dedupe_key="build:snapshot:3",
        worker_id="host:1:abcdef",
        run_after=NOW,
        lease_until=None,
        heartbeat_at=NOW,
        error="ValueError: secret detail",
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        finished_at=None,
    )
    view = JobView.of(job).model_dump(mode="json")
    assert "payload" not in view and "worker_id" not in view and "dedupe_key" not in view
    assert view["error"].startswith("ValueError: the build failed")
    assert (view["status"], view["stage"], view["progress"], view["attempts"]) == (
        "queued",
        "parse",
        0.4,
        1,
    )
