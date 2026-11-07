"""Job progress: ``GET /jobs/{job_id}``."""

from __future__ import annotations

from fastapi import APIRouter

from repoatlas.api.deps import EngineDep
from repoatlas.api.errors import documented, not_found
from repoatlas.api.schemas import JobIdPath, JobView
from repoatlas.jobs.queue import get_job

router = APIRouter(tags=["builds"])


@router.get("/jobs/{job_id}", summary="A job's status and progress", responses=documented(404))
def job_status(job_id: JobIdPath, engine: EngineDep) -> JobView:
    """Status (``queued``, ``running``, ``succeeded``, ``failed``), current stage, progress
    from 0 to 1, attempts, and why the last attempt failed."""
    job = get_job(job_id, engine=engine)
    if job is None:
        raise not_found("job", f"Job {job_id} does not exist.")
    return JobView.of(job)
