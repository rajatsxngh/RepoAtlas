"""Worker parts that need no database: stop signals, the stop file, and the handler registry."""

from __future__ import annotations

import os
import re
import signal
from collections.abc import Iterator
from pathlib import Path

import pytest

from repoatlas.cli import _install_stop_signals
from repoatlas.jobs import worker as worker_module
from repoatlas.jobs.worker import (
    JobHandler,
    JobInterrupted,
    Worker,
    default_worker_id,
    job_handlers,
    register_job_handler,
    unregister_job_handler,
)
from tests.unit.api.helpers import api_settings


def stopping(worker: Worker) -> bool:
    """Read ``stopping`` afresh (mypy would narrow a repeated property read)."""
    return worker.stopping


@pytest.fixture
def worker(tmp_path: Path) -> Iterator[Worker]:
    built = Worker(api_settings(), handlers={}, stop_file=tmp_path / "worker.stop")
    yield built
    built.close()


def test_the_first_signal_stops_gracefully_and_the_second_at_once(worker: Worker) -> None:
    previous = _install_stop_signals(worker)
    try:
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        assert not stopping(worker)
        handler(signal.SIGINT, None)
        assert stopping(worker)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
        assert signal.SIGINT in previous
        if hasattr(signal, "SIGBREAK"):  # Ctrl+Break on Windows
            assert signal.SIGBREAK in previous
    finally:
        for number, original in previous.items():
            signal.signal(number, original)
    assert signal.getsignal(signal.SIGINT) is previous[signal.SIGINT]


def test_a_stop_file_counts_only_when_it_appears_or_changes(tmp_path: Path) -> None:
    stop = tmp_path / "worker.stop"
    fresh = Worker(api_settings(), handlers={}, stop_file=stop)
    assert not stopping(fresh)
    stop.write_text("stop")
    assert stopping(fresh)
    fresh.close()

    stale = Worker(api_settings(), handlers={}, stop_file=stop)  # the file is already there
    assert not stopping(stale)
    os.utime(stop, ns=(stop.stat().st_atime_ns, stop.stat().st_mtime_ns + 1_000_000_000))
    assert stopping(stale)  # touched
    stale.close()


def test_intervals_are_checked(worker: Worker) -> None:
    settings = api_settings()
    with pytest.raises(ValueError, match="Heartbeats"):
        Worker(settings, handlers={}, lease_s=10, heartbeat_s=10)
    with pytest.raises(ValueError, match="timeout"):
        Worker(settings, handlers={}, timeout_s=0)
    assert (worker.lease_s, worker.heartbeat_s, worker.timeout_s) == (60, 15, 3600)
    assert worker.backoff_s == (10.0, 60.0, 300.0) and worker.poll_s == 2


def test_handlers_register_once_by_kind() -> None:
    handler = JobHandler("test-kind", lambda _ctx: None)
    register_job_handler(handler)
    try:
        assert job_handlers()["test-kind"] is handler
        with pytest.raises(ValueError, match="already registered"):
            register_job_handler(JobHandler("test-kind", lambda _ctx: None))
    finally:
        unregister_job_handler("test-kind")
    assert "test-kind" not in job_handlers()


def test_worker_ids_name_the_host_and_process() -> None:
    worker_id = default_worker_id()
    assert re.fullmatch(rf".+:{os.getpid()}:[0-9a-f]{{6}}", worker_id)
    assert default_worker_id() != worker_id


def test_interruptions_say_why() -> None:
    assert JobInterrupted("stop").reason == "stop"
    assert str(JobInterrupted("timeout")) == "The attempt ran out of time."
    assert worker_module.REPORT_INTERVAL_S == 1.0
