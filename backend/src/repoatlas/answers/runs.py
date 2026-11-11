"""Saving and reading answer runs (``qa_runs``)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Engine, insert, select

from repoatlas.answers.models import QaRunRow
from repoatlas.answers.schema import AnswerResult


class QaRun(BaseModel):
    id: int
    snapshot_id: int
    mode: str
    question: str
    result: AnswerResult
    created_at: datetime


def save_run(
    engine: Engine, snapshot_id: int, mode: str, question: str, result: AnswerResult
) -> int:
    """Store one run; its ID."""
    with engine.begin() as connection:
        run_id: int = connection.execute(
            insert(QaRunRow)
            .values(
                snapshot_id=snapshot_id,
                mode=mode,
                question=question,
                answer=result.model_dump(mode="json"),
                refused=result.refused,
                refusal_kind=result.refusal_kind,
                prefilter_citation_validity=result.metrics.prefilter_citation_validity,
                dropped_claims=result.metrics.claims_dropped,
                dropped_claim_rate=result.metrics.dropped_claim_rate,
                cost_usd=result.cost_usd,
                trace_id=result.trace_id,
            )
            .returning(QaRunRow.id)
        ).scalar_one()
    return run_id


def load_run(engine: Engine, run_id: int) -> QaRun | None:
    with engine.connect() as connection:
        row = connection.execute(select(QaRunRow).where(QaRunRow.id == run_id)).first()
    if row is None:
        return None
    return QaRun(
        id=row.id,
        snapshot_id=row.snapshot_id,
        mode=row.mode,
        question=row.question,
        result=AnswerResult.model_validate(row.answer),
        created_at=row.created_at,
    )
