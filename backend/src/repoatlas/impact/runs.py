"""Running, saving, and reading impact analyses (``impact_runs``), each with a trace."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Engine, insert, select

from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.models import ImpactRunRow
from repoatlas.impact.schema import DiffTarget, ImpactParams, ImpactReport, SymbolTarget
from repoatlas.tracing import TraceSink, start_trace, trace_span


class ImpactRun(BaseModel):
    id: int
    snapshot_id: int
    report: ImpactReport
    trace_id: uuid.UUID | None
    created_at: datetime


def analyze_traced(
    engine: ImpactEngine,
    snapshot_id: int,
    target: SymbolTarget | DiffTarget,
    params: ImpactParams | None = None,
    *,
    sink: TraceSink | None = None,
) -> tuple[ImpactReport, uuid.UUID]:
    """Analyze inside an ``impact`` trace (written to ``sink`` when it ends)."""
    with (
        start_trace("impact", snapshot_id, sink=sink) as trace,
        trace_span("impact.analyze", "impact", target_kind=target.kind) as span,
    ):
        report = engine.analyze(snapshot_id, target, params)
        span.set(
            targets=report.stats.targets,
            items=report.stats.items,
            unknown=report.stats.unknown,
            expanded=report.stats.expanded,
        )
    return report, trace.id


def save_impact_run(
    db: Engine, snapshot_id: int, report: ImpactReport, trace_id: uuid.UUID | None
) -> int:
    with db.begin() as connection:
        run_id: int = connection.execute(
            insert(ImpactRunRow)
            .values(
                snapshot_id=snapshot_id,
                target=report.target.model_dump(mode="json"),
                report=report.model_dump(mode="json"),
                trace_id=trace_id,
            )
            .returning(ImpactRunRow.id)
        ).scalar_one()
    return run_id


def load_impact_run(db: Engine, run_id: int) -> ImpactRun | None:
    with db.connect() as connection:
        row = connection.execute(select(ImpactRunRow).where(ImpactRunRow.id == run_id)).first()
    if row is None:
        return None
    return ImpactRun(
        id=row.id,
        snapshot_id=row.snapshot_id,
        report=ImpactReport.model_validate(row.report),
        trace_id=row.trace_id,
        created_at=row.created_at,
    )
