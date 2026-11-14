"""A scripted model and an in-memory repository for agent tests: no database, no model."""

from __future__ import annotations

from typing import Any, cast

from repoatlas.agent.ledger import Ledger
from repoatlas.agent.loop import AgentConfig, Explorer
from repoatlas.agent.results import ToolError, ToolResult
from repoatlas.agent.tools import Toolbox
from repoatlas.llm.fake import FakeCall, FakeLLM, Script
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import LLMResult, ToolCall, Usage
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.tracing.core import Trace
from tests.unit.answers.helpers import APP, FILES, UTIL, FakeBlobs, oid

SMALL = "claude-haiku-5-5"
LARGE = "claude-sonnet-5-5"
QUESTION = "How is the app created?"
CREATE_APP_KEY = "py:src/app.py:create_app:function"
APP_KEY = "py:src/app.py:App:class"
STEP_USAGE = Usage(input_tokens=1_000, output_tokens=100)
THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc123"}


def ref(path: str, start: int, end: int, key: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        blob_oid=oid(FILES[path]), path=path, start_line=start, end_line=end, symbol_key=key
    )


CREATE_APP_REF = ref("src/app.py", 4, 8, CREATE_APP_KEY)
APP_REF = ref("src/app.py", 11, 17, APP_KEY)
SLUGIFY_REF = ref("src/util.py", 1, 2, "py:src/util.py:slugify:function")
assert APP and UTIL


class FakeToolbox:
    """Tools over the two files of ``tests.unit.answers.helpers``. ``calls`` records what
    actually ran (a call the loop short-circuits never gets here)."""

    def __init__(self, results: dict[str, ToolResult | ToolError] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: dict[str, ToolResult | ToolError] = {
            "search_code": ToolResult(
                content="Results, best first: 2.\n\n[1] src/app.py lines 4-8\n4 | def create_app",
                evidence=[CREATE_APP_REF, APP_REF],
            ),
            "find_definition": ToolResult(
                content=f"Matches for 'create_app', best first: 1.\n- {CREATE_APP_KEY}",
                evidence=[CREATE_APP_REF],
            ),
            "find_callees": ToolResult(
                content=f"Symbols called from {CREATE_APP_KEY}, up to 1 calls away: 1.",
                evidence=[APP_REF],
            ),
            "read_file": ToolResult(
                content="src/util.py lines 1-2 (the file has 2 lines).\n1 | def slugify(text):",
                evidence=[SLUGIFY_REF],
            ),
            **(results or {}),
        }

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self.results:
            raise ToolError(f"There is no tool named {name!r}.", allowed=False)
        self.calls.append((name, arguments))
        result = self.results[name]
        if isinstance(result, ToolError):
            raise result
        return result


def call(tool: str, call_id: str = "", /, **arguments: Any) -> ToolCall:
    """A scripted tool call (positional-only, so a tool argument may be called ``name``)."""
    return ToolCall(id=call_id or f"toolu_{tool}", name=tool, input=arguments)


def turn(
    *calls: ToolCall,
    text: str = "",
    thinking: bool = False,
    usage: Usage = STEP_USAGE,
    stop_reason: str | None = None,
    model: str = SMALL,
) -> LLMResult:
    """One scripted exploration turn of the small model."""
    blocks: list[dict[str, Any]] = [dict(THINKING)] if thinking else []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls)
    return LLMResult(
        content=text,
        tool_calls=list(calls),
        usage=usage,
        cost_usd=PriceTable(DEFAULT_PRICES).cost(usage, model),
        stop_reason=stop_reason or ("tool_use" if calls else "end_turn"),
        thinking_blocks=[dict(THINKING)] if thinking else [],
        blocks=blocks,
        model=model,
        latency_ms=1.0,
    )


def done() -> LLMResult:
    return turn(call("final_answer"))


class Sink:
    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def write(self, trace: Trace) -> None:
        self.traces.append(trace)


def explorer(
    script: list[Script] | Any,
    toolbox: FakeToolbox | None = None,
    **config: Any,
) -> tuple[Explorer, FakeLLM, FakeToolbox]:
    llm = FakeLLM(script)
    box = toolbox or FakeToolbox()
    found = Explorer(
        llm,
        lambda snapshot_id: cast(Toolbox, box),
        lambda: Ledger(FakeBlobs()),
        prices=PriceTable(DEFAULT_PRICES),
        config=AgentConfig(**config),
    )
    return found, llm, box


def tool_results(fake_call: FakeCall) -> list[dict[str, Any]]:
    """The tool results in the last user turn a call saw."""
    content = fake_call.messages[-1]["content"]
    return [block for block in content if block.get("type") == "tool_result"]


def last_text(fake_call: FakeCall) -> str:
    """The closing text block of the last user turn a call saw ('' when there is none)."""
    content = fake_call.messages[-1]["content"]
    texts = [block["text"] for block in content if block.get("type") == "text"]
    return texts[-1] if texts else ""
