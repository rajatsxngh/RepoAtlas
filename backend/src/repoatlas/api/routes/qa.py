"""Questions about a snapshot (ANS-11): ask with streamed status, stored runs, and traces.

``POST /snapshots/{id}/ask`` answers with server-sent events:

- ``status`` — ``{"step": "retrieving" | "answering" | "validating"}`` as each step starts;
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

from repoatlas.answers.factory import oneshot_from_settings
from repoatlas.answers.runs import load_run, save_run
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.system import AnswerSystem
from repoatlas.api.deps import EngineDep, ReadySnapshot, SettingsDep
from repoatlas.api.errors import ApiError, ErrorCode, documented, from_store_error, not_found
from repoatlas.api.schemas import QaRunView, answer_view
from repoatlas.llm.client import LLMNotConfigured
from repoatlas.llm.types import LLMError, TransientLLMError
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


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question = Field(description="The question, in plain language.")
    mode: Literal["oneshot"] = Field(default="oneshot", description="The answering mode.")


def answer_system(request: Request, mode: str) -> AnswerSystem:
    """The app's answer system for ``mode``, built on first use (503 without an API key)."""
    state = request.app.state
    systems = cast(dict[str, AnswerSystem], state.answer_systems)
    with _build_lock:
        if mode not in systems:
            systems[mode] = _build(state)
    return systems[mode]


def _build(state: Any) -> AnswerSystem:
    try:
        return oneshot_from_settings(state.settings, state.engine, state.store)
    except LLMNotConfigured:
        raise ApiError(
            503,
            ErrorCode.LLM_UNAVAILABLE,
            "Answers are not set up on this server: it has no Anthropic API key.",
        ) from None


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
    "Server-sent events: `status` (`{step}`), `answer` (an `AnswerView`), `done` "
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

                result: AnswerResult = await system.answer(
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
