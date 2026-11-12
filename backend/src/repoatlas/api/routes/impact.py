"""Impact analysis (IMP-6): ``POST /snapshots/{id}/impact`` with a symbol key or a diff.

The answer is the ``ImpactReport`` plus the stored run's ID and its trace: ranked items in three
groups (tests, public APIs, callers), each with its best path and evidence, and the unknown
sites the graph could not resolve. Every run is stored in ``impact_runs``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, Self, cast

from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from repoatlas.api.deps import EngineDep, ReadySnapshot
from repoatlas.api.errors import documented
from repoatlas.api.schemas import MAX_KEY_CHARS, no_nul
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.runs import analyze_traced, save_impact_run
from repoatlas.impact.schema import (
    MAX_DIFF_CHARS,
    DiffTarget,
    ImpactParams,
    ImpactReport,
    SymbolTarget,
)
from repoatlas.tracing.store import SqlTraceSink

router = APIRouter(tags=["impact"])


class ImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol_key: Annotated[
        str | None,
        Field(min_length=1, max_length=MAX_KEY_CHARS, description="The symbol that changes."),
        AfterValidator(lambda v: v if v is None else no_nul(v)),
    ] = None
    diff: Annotated[
        str | None,
        Field(min_length=1, max_length=MAX_DIFF_CHARS, description="A unified diff."),
        AfterValidator(lambda v: v if v is None else no_nul(v)),
    ] = None
    side: Literal["old", "new"] = Field(
        default="old",
        description="For a diff: `old` when the snapshot is the code the diff applies to, "
        "`new` when it already contains the change.",
    )
    params: ImpactParams | None = Field(default=None, description="Search settings.")

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.symbol_key is None) == (self.diff is None):
            raise ValueError("Send exactly one of symbol_key and diff.")
        return self

    def target(self) -> SymbolTarget | DiffTarget:
        if self.symbol_key is not None:
            return SymbolTarget(symbol_key=self.symbol_key)
        assert self.diff is not None
        return DiffTarget(diff=self.diff, side=self.side)


class ImpactResponse(ImpactReport):
    run_id: int
    trace_id: uuid.UUID


def impact_engine(request: Request) -> ImpactEngine:
    return cast(ImpactEngine, request.app.state.impact_engine)


@router.post(
    "/snapshots/{snapshot_id}/impact",
    summary="What breaks if this changes",
    responses=documented(404, 409, 422),
)
def analyze_impact(
    snapshot_id: ReadySnapshot, body: ImpactRequest, request: Request, engine: EngineDep
) -> ImpactResponse:
    """Tests, public APIs, and callers a change to a symbol (or the code a diff touches) may
    break, ranked by score, with paths, evidence, and the sites the graph could not resolve."""
    report, trace_id = analyze_traced(
        impact_engine(request),
        snapshot_id,
        body.target(),
        body.params,
        sink=SqlTraceSink(engine),
    )
    run_id = save_impact_run(engine, snapshot_id, report, trace_id)
    return ImpactResponse(**report.model_dump(), run_id=run_id, trace_id=trace_id)
