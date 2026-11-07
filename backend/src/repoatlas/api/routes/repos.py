"""Build requests (API-2): ``POST /repos``."""

from __future__ import annotations

from fastapi import APIRouter

from repoatlas.api.deps import EngineDep, SettingsDep
from repoatlas.api.errors import documented
from repoatlas.api.schemas import BuildAccepted, BuildRequestBody
from repoatlas.jobs.build import request_build

router = APIRouter(tags=["builds"])


@router.post(
    "/repos",
    status_code=202,
    summary="Build a repository's snapshot",
    responses=documented(403, 404, 413, 502, 503),
)
def build_repo(body: BuildRequestBody, settings: SettingsDep, engine: EngineDep) -> BuildAccepted:
    """Check the URL and the repository on GitHub (public, under the size cap), pin the
    default branch's head, and reuse the snapshot of that commit built with this pipeline's
    configuration: ready, or with its build already queued or running. Otherwise a build is
    queued. Follow the job (or the snapshot) until the snapshot is ready."""
    result = request_build(body.url, settings, engine=engine)
    return BuildAccepted(
        snapshot_id=result.snapshot_id,
        job_id=result.job_id,
        reused=result.reused,
        status=result.status,
    )
