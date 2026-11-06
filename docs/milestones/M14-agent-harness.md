# M14 — Agent harness

**Type of work:** AI agent · **Size:** L (10 tasks) · **Status:** Not started

## Goal

Let a tool-using agent explore the graph for questions one retrieval pass can't answer — with hard budgets, the same citation guarantees as one-shot answers, and a measured comparison against one-shot retrieval. Expose the same tools to other coding agents through MCP.

## Depends on

- **M13** — injection gate (live `injection` suite, `canary_leak` scorer), allowlist helpers and argument validators, `TracePanel`, flagged evidence in prompts.
- **M11** — `ImpactEngine.analyze` (for the `get_impact` tool).
- **M10** — answer-system registry, runner, compare, reports.
- **M07** — `LLMClient` (tool calls, thinking blocks, refusals, routing), `AnswerDraft`/`AnswerResult`, `CitationValidator`, `RefusalPolicy`, tracing, ask API with server-sent events, `AskPanel`.
- **M06** — `Retriever` (for `search_code`), `EvidenceRef`.
- **M03** — `GraphStore` (`find_symbols`, `callers`, `callees`, `shortest_path`, `read_code`, `owners`).

## Provides

- **Tools** (`repoatlas.agent.tools`) — flat JSON schemas plus implementations returning `ToolResult(content: str, evidence: list[EvidenceRef], truncated: bool)`:

  | Tool | Arguments | Returns |
  |---|---|---|
  | `search_code` | `query`, `k ≤ 10` | Ranked evidence from hybrid retrieval |
  | `find_definition` | `name` | Matching symbols with locations |
  | `find_callers` | `symbol_key`, `depth ≤ 3` | Callers with edge confidence and call sites |
  | `find_callees` | `symbol_key`, `depth ≤ 3` | Callees with edge confidence and call sites |
  | `trace_path` | `from_key`, `to_key` | Path of symbols and edges |
  | `read_file` | `path`, `start`, `end` (≤ 200 lines) | Code with line numbers |
  | `get_owners` | `path` or `symbol_key` | Top authors with shares |
  | `get_impact` | `symbol_key`, `limit ≤ 30` | Grouped impact items and unknown sites |

- **`repoatlas.agent.loop.AgentAnswerSystem`** — config `max_steps = 12`, `max_input_tokens`, `per_tool_result_tokens`, roles (`small` for steps, `large` for the final answer); an evidence ledger; final structured answer validated by `CitationValidator`; registered as `agent`.
- **API and UI:** `mode = "agent"` on the ask endpoint; server-sent `step` events (tool, argument summary, result size, tokens, cost); the UI mode toggle and live steps in `TracePanel`.
- **MCP server:** `repoatlas mcp --snapshot <id> [--transport stdio|http] [--port N]` exposing the same tools; HTTP requires a bearer token and is rate-limited; the MCP SDK is pinned to one major version.
- **Results:** live injection suite results for agent mode; agent vs one-shot report on the test split.

## Requirements

AGT-1, AGT-2, AGT-3, AGT-4, AGT-5, AGT-6, AGT-7, AGT-8

## Scope

**In:** tools, argument validation and allowlists, result trimming, agent loop with budgets and routing, evidence ledger, API/UI agent mode, MCP server, injection gate, evaluation.

**Out:** multiple cooperating agents, cost optimization beyond the budgets defined here.

## Design notes

### Loop rules
- Messages are append-only: earlier turns are never edited or reordered. Tool results are trimmed **before** they are appended (to `per_tool_result_tokens`, keeping the head and the evidence list, with a note on what was cut).
- Thinking blocks are passed back exactly as the model returned them.
- Stop when the model calls the `final_answer` tool, when `max_steps` is reached, or when the token or spend budget runs out; on a budget stop, the system answers from the ledger with the `large` model if evidence exists, otherwise refuses with `budget`.
- Repeated identical tool calls (same tool and arguments twice in a row) are short-circuited with a cached result and a note.

### Evidence ledger
- Every tool result's evidence references are added to a ledger with stable IDs (`E1…`). The final answer may cite only ledger IDs; the validator checks them exactly as for one-shot answers.

### Safety
- Tools are read-only and limited to the snapshot (allowlist helpers); arguments are validated before execution; tool output passes through the injection detector so flagged spans are marked in what the model sees.
- The agent never fetches URLs or runs code.

### Routing
- Exploration steps use the `small` role; the final synthesis uses `large`. Both are traced; cost per step appears in the trace panel.

### MCP
- Uses the official Python MCP SDK, pinned to its current major version; tools are registered with the same schemas and implementations; stdio for local clients (such as Claude Code) and Streamable HTTP for remote use with a bearer token and rate limits.
- Tested with the SDK's in-memory client; checked by hand from Claude Code.

### Evaluation
- `agent` vs `oneshot` (the best one-shot configuration) on the test split, three runs each, per question type, with 95% intervals and cost per question. If the agent doesn't win on multi-hop questions, the report says so and the analysis explains where it fails.

## Tasks

- [ ] **T1 — Tools.** Implementations and schemas; unit tests per tool on the fixture repos.
- [ ] **T2 — Validation, allowlists, trimming.** Argument validators, snapshot allowlist, result trimming, detector pass on tool output.
- [ ] **T3 — Agent loop.** Steps, stop conditions, budgets, routing, repeated-call guard; scripted `FakeLLM` tests (normal finish, step cap, budget stop, tool error).
- [ ] **T4 — Evidence ledger and final answer.** Validation of ledger citations; refusal rules.
- [ ] **T5 — API agent mode.** `step` events over server-sent events; trace linkage.
- [ ] **T6 — UI.** Mode toggle; live steps in `TracePanel`; Playwright test with mock mode.
- [ ] **T7 — MCP server.** stdio and HTTP transports, bearer token and rate limits; in-memory client test.
- [ ] **T8 — Manual MCP check.** Connect from Claude Code and run three questions; notes in Results.
- [ ] **T9 — Injection gate.** Live injection suite in agent mode; zero leaks.
- [ ] **T10 — Evaluation.** Agent vs one-shot on the test split; report and summary in Results.

## Exit criteria

- [ ] Tool, loop, and cap tests pass with the scripted fake LLM.
- [ ] Live injection suite: zero canary leaks in agent mode.
- [ ] The MCP in-memory client test passes, and the manual Claude Code check is recorded.
- [ ] A test-split report compares agent and one-shot with 95% intervals and cost per question.

## Verification

```bash
cd backend
uv run pytest tests/unit/agent tests/integration/mcp -q
uv run repoatlas eval run --suite injection --system agent --max-cost 3.00
uv run repoatlas eval run --system agent --split test --runs 3 --max-cost 25.00
uv run repoatlas mcp --snapshot <id> --transport stdio
```

## Risks and open questions

- Agents can loop or wander; step caps, the repeated-call guard, and budgets bound the cost.
- Model behavior for tool use may change; recorded tests catch regressions.
- Open: the right `max_steps` and per-tool budgets; tuned on dev.

## Results

_Filled in at the gate._
