"""The launch gallery (LCH-2): ``GET /gallery``."""

from __future__ import annotations

from fastapi import APIRouter

from repoatlas.api.deps import EngineDep
from repoatlas.api.errors import documented
from repoatlas.api.schemas import Gallery
from repoatlas.gallery.store import read_gallery

router = APIRouter(tags=["gallery"])


@router.get("/gallery", summary="Precomputed repositories", responses=documented())
def get_gallery(engine: EngineDep) -> Gallery:
    """Well-known repositories built ahead of time at pinned commits, in display order. Every
    entry's snapshot is ready, so it opens without a build. The titles and descriptions come
    from the deployment's gallery list, not from the repositories. Empty when nothing was
    precomputed on this deployment."""
    return Gallery(entries=read_gallery(engine=engine))
