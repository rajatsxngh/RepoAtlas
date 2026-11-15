"""Questions about a snapshot (ANS-11): ask with streamed status, stored runs, and traces.

``POST /snapshots/{id}/ask`` answers with server-sent events:

- ``status`` — ``{"step": …}`` as each step starts: ``retrieving``, ``answering``,
  ``validating`` for one-shot answers; ``exploring``, ``answering``, ``validating`` in agent
  mode; ``planning``, ``exploring``, ``writing``, ``verifying``, ``validating`` in multi-agent
  mode;
- ``plan`` — multi-agent mode only (MAS-6): once, the plan's branches (``{"branches": [{id,
  sub_question, seed_symbols, max_steps}]}``), before they are explored;
- ``step`` — agent and multi-agent mode (AGT-6, MAS-6): one event per tool call, one when the
  model ends the exploration, and one for the final answer call, as they finish
  (``StepEvent``: the tool, a summary of its arguments, the size of its result, the evidence
  it listed, and the tokens and cost of the model turn behind it). ``span_id`` is the step's
  span in the answer's trace. In multi-agent mode the events of a branch carry its ID in
  ``branch`` (branches run at the same time, so their events arrive mixed), and the plan, each
  branch's notes, the writing call, and each verification and repair call are events too;
- ``answer`` — the validated answer (``AnswerView``), refused or not;
- ``done`` — ``{"run_id", "trace_id"}`` once the run is stored;
- ``error`` — ``{"error": {"code", "message", "details"}}`` if answering failed.

While a step takes long, a comment line (``: keep-alive``) is sent every 15 seconds, which
clients ignore and which keeps proxies from closing an idle stream.

Raw model tokens are not streamed: the answer is shown only after its citations are checked.
Errors found before the stream starts (unknown snapshot, not ready, invalid body, answers not
configured) are ordinary error responses. Each run is stored in ``qa_runs``
(``GET /qa/{run_id}``); its steps, tokens, cost, and time are a trace (``GET /traces/{id}``).
"""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, cast

import anyio
import structlog
from fastapi import APIRouter, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from repoatlas.agent.factory import agent_budget, agent_from_settings
from repoatlas.agent.loop import AgentAnswerSystem, StepEvent
from repoatlas.answers.factory import oneshot_from_settings
from repoatlas.answers.runs import load_run, save_run
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.system import AnswerSystem
from repoatlas.api.deps import EngineDep, ReadySnapshot, SettingsDep
from repoatlas.api.errors import ApiError, ErrorCode, documented, from_store_error, not_found
from repoatlas.api.schemas import QaRunView, answer_view
from repoatlas.llm.client import LLMNotConfigured
from repoatlas.llm.types import LLMError, TransientLLMError
from repoatlas.multiagent.factory import multiagent_budget, multiagent_from_settings
from repoatlas.multiagent.models import Plan
from repoatlas.multiagent.system import MultiAgentAnswerSystem
from repoatlas.store.errors import StoreError
from repoatlas.tracing.store import TraceView, load_trace

log = structlog.get_logger(__name__)
router = APIRouter(tags=["answers"])
MAX_QUESTION_CHARS = 2_000
KEEP_ALIVE_S = 15.0  # silence on the stream before a keep-alive comment
KEEP_ALIVE = ": keep-alive\n\n"
_build_lock = threading.Lock()

Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]


AskMode = Literal["oneshot", "agent", "multiagent"]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question = Field(description="The question, in plain language.")
    mode: AskMode = Field(
        default="oneshot",
        description=(
            "The answering mode: `oneshot` retrieves once and answers; `agent` explores the "
            "code with tools first (slower, for questions one search can't answer); "
            "`multiagent` splits the question across explorers that work side by side and "
            "checks every claim of the answer against the code it cites (slowest, for "
            "questions with independent parts)."
        ),
    )


def answer_system(request: Request, mode: str) -> AnswerSystem:
    """The app's answer system for ``mode``, built on first use (503 without an API key)."""
    state = request.app.state
    systems = cast(dict[str, AnswerSystem], state.answer_systems)
    with _build_lock:
        if mode not in systems:
            systems[mode] = _build(state, mode)
    return systems[mode]


def _build(state: Any, mode: str) -> AnswerSystem:
    try:
        if mode == "agent":
            return agent_from_settings(state.settings, state.engine, state.store)
        if mode == "multiagent":
            return multiagent_from_settings(state.settings, state.engine, state.store)
        return oneshot_from_settings(state.settings, state.engine, state.store)
    except LLMNotConfigured:
        raise ApiError(
            503,
            ErrorCode.LLM_UNAVAILABLE,
            "Answers are not set up on this server: it has no Anthropic API key.",
        ) from None


def plan_view(plan: Plan) -> dict[str, Any]:
    """A plan as the ``plan`` event shows it: what each branch will look into, and for how
    many steps (its token and money shares stay on the server)."""
    return {
        "branches": [
            {
                "id": branch.id,
                "sub_question": branch.sub_question,
                "seed_symbols": branch.seed_symbols,
                "max_steps": branch.budget.max_steps,
            }
            for branch in plan.branches
        ]
    }


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _error_event(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, StoreError):
        error = from_store_error(exc)
    elif isinstance(exc, TransientLLMError):
        error = ApiError(
            503, ErrorCode.ANSWER_FAILED, "The model is busy or unreachable; try again shortly."
        )
    elif isinstance(exc, LLMError):
        error = ApiError(502, ErrorCode.ANSWER_FAILED, "The model could not answer this question.")
    else:
        error = ApiError(500, ErrorCode.INTERNAL, "Answering failed unexpectedly.")
    return {"error": {"code": error.code, "message": error.message, "details": error.details}}


ASK_DESCRIPTION = (
    "Server-sent events: `status` (`{step}`), in agent and multi-agent mode `step` (one per "
    "tool call and one per model stage; in multi-agent mode with the `branch` it belongs to), "
    "in multi-agent mode `plan` (`{branches}`), `answer` (an `AnswerView`), `done` "
    "(`{run_id, trace_id}`), or `error` (the error model)."
)


@router.post(
    "/snapshots/{snapshot_id}/ask",
    response_class=StreamingResponse,
    responses={
        200: {"description": ASK_DESCRIPTION, "content": {"text/event-stream": {}}},
        **documented(404, 409, 422, 429, 503),
    },
)
async def ask(
    snapshot_id: ReadySnapshot,
    body: AskRequest,
    request: Request,
    settings: SettingsDep,
    engine: EngineDep,
) -> StreamingResponse:
    """Answer a question about the snapshot with cited code, streaming each step."""
    system = await anyio.to_thread.run_sync(answer_system, request, body.mode)
    if body.mode == "agent":
        budget = agent_budget(settings)
    elif body.mode == "multiagent":
        budget = multiagent_budget(settings)
    else:
        budget = Budget(
            max_cost_usd=settings.answer_max_cost_usd,
            max_output_tokens=settings.answer_max_output_tokens,
        )
    send, receive = anyio.create_memory_object_stream[str](16)

    async def run() -> None:
        async with send:
            try:

                async def on_status(step: str) -> None:
                    await send.send(sse("status", {"step": step}))

                async def on_step(event: StepEvent) -> None:
                    await send.send(sse("step", event.model_dump(mode="json")))

                async def on_plan(plan: Plan) -> None:
                    await send.send(sse("plan", plan_view(plan)))

                result: AnswerResult
                if isinstance(system, MultiAgentAnswerSystem):
                    result = await system.answer(
                        snapshot_id,
                        body.question,
                        budget,
                        on_status=on_status,
                        on_step=on_step,
                        on_plan=on_plan,
                    )
                elif isinstance(system, AgentAnswerSystem):
                    result = await system.answer(
                        snapshot_id, body.question, budget, on_status=on_status, on_step=on_step
                    )
                else:
                    result = await system.answer(
                        snapshot_id, body.question, budget, on_status=on_status
                    )
                run_id = await anyio.to_thread.run_sync(
                    save_run, engine, snapshot_id, body.mode, body.question, result
                )
                view = answer_view(result, run_id=run_id)
                await send.send(sse("answer", view.model_dump(mode="json")))
                trace = str(result.trace_id) if result.trace_id else None
                await send.send(sse("done", {"run_id": run_id, "trace_id": trace}))
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:
                await send.send(sse("error", _error_event(exc)))
                # A log stream that can't be written (closed) must not break the response.
                with contextlib.suppress(ValueError, OSError):
                    log.exception("ask_failed", snapshot_id=snapshot_id)

    async def events() -> AsyncIterator[str]:
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            async with receive:
                while True:
                    # A comment line while nothing else is sent, so proxies with idle
                    # timeouts keep the stream open during a long model call.
                    with anyio.move_on_after(KEEP_ALIVE_S) as waiting:
                        try:
                            chunk = await receive.receive()
                        except anyio.EndOfStream:
                            break
                    yield KEEP_ALIVE if waiting.cancelled_caught else chunk

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


RunIdPath = Annotated[int, Path(ge=1, le=2**63 - 1, description="The run's ID.")]


@router.get("/qa/{run_id}", responses=documented(404))
def get_qa_run(run_id: RunIdPath, engine: EngineDep) -> QaRunView:
    """A stored answer with its citations and metrics."""
    run = load_run(engine, run_id)
    if run is None:
        raise not_found("qa_run", f"Run {run_id} does not exist.", run_id=run_id)
    return QaRunView(
        id=run.id,
        snapshot_id=run.snapshot_id,
        mode=run.mode,
        question=run.question,
        created_at=run.created_at,
        answer=answer_view(run.result, run_id=run.id),
    )


@router.get("/traces/{trace_id}", responses=documented(404))
def get_trace(trace_id: uuid.UUID, engine: EngineDep) -> TraceView:
    """A trace with its spans: steps, models, tokens, cost, and time."""
    trace = load_trace(engine, trace_id)
    if trace is None:
        raise not_found("trace", f"Trace {trace_id} does not exist.", trace_id=str(trace_id))
    return trace
