"""Impact analysis (IMP-6): ``POST /snapshots/{id}/impact`` with a symbol key or a diff.

The answer is the ``ImpactReport`` plus the stored run's ID and its trace: ranked items in three
groups (tests, public APIs, callers), each with its best path and evidence, and the unknown
sites the graph could not resolve. Every run is stored in ``impact_runs``.

``POST /snapshots/{id}/impact/{run_id}/explain`` (GRD-1) returns a cited explanation of a
stored run: the ``large`` model summarizes the report, and the answer goes through the same
citation validation and refusal policy as every answer (``repoatlas.impact.explain``). It calls
the model, so it counts as a question for the rate limits and the daily spend cap. The
explanation is stored in ``qa_runs`` with mode ``impact_explain`` and has its own trace.
"""

from __future__ import annotations

import threading
import uuid
from typing import Annotated, Literal, Self, cast

import anyio
from fastapi import APIRouter, Path, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from repoatlas.answers.runs import save_run
from repoatlas.answers.schema import Budget
from repoatlas.api.deps import EngineDep, ReadySnapshot, SettingsDep
from repoatlas.api.errors import ApiError, ErrorCode, documented, not_found
from repoatlas.api.schemas import MAX_KEY_CHARS, AnswerView, answer_view, no_nul
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.explain import ImpactExplainer, explainer_from_settings, impact_question
from repoatlas.impact.runs import analyze_traced, load_impact_run, save_impact_run
from repoatlas.impact.schema import (
    MAX_DIFF_CHARS,
    DiffTarget,
    ImpactParams,
    ImpactReport,
    SymbolTarget,
)
from repoatlas.llm.client import LLMNotConfigured
from repoatlas.llm.types import LLMError, TransientLLMError
from repoatlas.tracing.store import SqlTraceSink

EXPLAIN_MODE = "impact_explain"
_build_lock = threading.Lock()

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


def impact_explainer(request: Request) -> ImpactExplainer:
    """The app's explainer, built on first use (503 without an API key)."""
    state = request.app.state
    with _build_lock:
        if state.impact_explainer is None:
            try:
                state.impact_explainer = explainer_from_settings(
                    state.settings, state.engine, state.store
                )
            except LLMNotConfigured:
                raise ApiError(
                    503,
                    ErrorCode.LLM_UNAVAILABLE,
                    "Explanations are not set up on this server: it has no Anthropic API key.",
                ) from None
    return cast(ImpactExplainer, state.impact_explainer)


ImpactRunPath = Annotated[int, Path(ge=1, le=2**63 - 1, description="The impact run's ID.")]


@router.post(
    "/snapshots/{snapshot_id}/impact/{run_id}/explain",
    summary="Explain an impact report",
    responses=documented(404, 409, 422, 429, 502, 503),
)
async def explain_impact_run(
    snapshot_id: ReadySnapshot,
    run_id: ImpactRunPath,
    request: Request,
    settings: SettingsDep,
    engine: EngineDep,
) -> AnswerView:
    """A short cited summary of a stored impact run: which tests, public APIs, and callers the
    change affects and why. Claims that can't be verified against the cited code are left
    out; a report with nothing to cite is refused without calling the model."""
    run = await anyio.to_thread.run_sync(load_impact_run, engine, run_id)
    if run is None or run.snapshot_id != snapshot_id:
        raise not_found(
            "impact_run",
            f"Snapshot {snapshot_id} has no impact run {run_id}.",
            snapshot_id=snapshot_id,
            run_id=run_id,
        )
    explainer = await anyio.to_thread.run_sync(impact_explainer, request)
    budget = Budget(
        max_cost_usd=settings.answer_max_cost_usd,
        max_output_tokens=settings.answer_max_output_tokens,
    )
    try:
        result = await explainer.explain(snapshot_id, run.report, budget)
    except TransientLLMError:
        raise ApiError(
            503, ErrorCode.ANSWER_FAILED, "The model is busy or unreachable; try again shortly."
        ) from None
    except LLMError:
        raise ApiError(
            502, ErrorCode.ANSWER_FAILED, "The model could not explain this report."
        ) from None
    question = impact_question(run.report, explainer.chosen_items(run.report))
    saved = await anyio.to_thread.run_sync(
        save_run, engine, snapshot_id, EXPLAIN_MODE, question, result
    )
    return answer_view(result, run_id=saved)
