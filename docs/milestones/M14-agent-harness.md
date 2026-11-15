# M14 — Agent harness

**Type of work:** AI agent · **Size:** L (10 tasks) · **Status:** Done

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

- **`repoatlas.agent.loop.AgentAnswerSystem`** — limits `AgentConfig(max_steps = 12, max_input_tokens, per_tool_result_tokens)`, roles (`small` for steps, `large` for the final answer); an evidence ledger (`repoatlas.agent.ledger.Ledger`, IDs `E1…`); final structured answer validated by `CitationValidator`; `answer(snapshot_id, question, budget, *, on_status=None, on_step=None)` where `on_step` hears a `StepEvent` per tool call and for the final call; registered as `agent`. Built from settings by `repoatlas.agent.factory` (`agent_from_settings`, `agent_system`, `toolbox_factory`, `agent_budget`).
- **Tool output for a model** (`repoatlas.agent.guard`): `check_arguments`, `prepare_result(result, evidence_lines, max_tokens=…)` (trim, detector marks, evidence list), `prepare_error`.
- **API and UI:** `mode = "agent"` on the ask endpoint; server-sent `step` events (tool, argument summary, result size, tokens, cost); the UI mode toggle and live steps in `TracePanel`.
- **MCP server:** `repoatlas mcp --snapshot <id> [--transport stdio|http] [--port N] [--host H]` exposing the same tools (`repoatlas.agent.mcp_server.build_server`); results carry the prepared text and the evidence as structured content, with compact evidence references as IDs; HTTP (Streamable HTTP at `/mcp`, stateless) requires the bearer token `REPOATLAS_MCP_TOKEN` and is rate-limited per client address (`REPOATLAS_MCP_CALLS_PER_MINUTE`); the MCP SDK is pinned to its 2.x major version. Documented in `docs/mcp.md`.
- **Results:** the injection suite and the agent-versus-one-shot comparison run end to end without a model (decision 0022); the live results are pending in [`docs/live-pass.md`](../live-pass.md).

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

- [x] **T1 — Tools.** Implementations and schemas; unit tests per tool on the fixture repos.
- [x] **T2 — Validation, allowlists, trimming.** Argument validators, snapshot allowlist, result trimming, detector pass on tool output.
- [x] **T3 — Agent loop.** Steps, stop conditions, budgets, routing, repeated-call guard; scripted `FakeLLM` tests (normal finish, step cap, budget stop, tool error).
- [x] **T4 — Evidence ledger and final answer.** Validation of ledger citations; refusal rules.
- [x] **T5 — API agent mode.** `step` events over server-sent events; trace linkage.
- [x] **T6 — UI.** Mode toggle; live steps in `TracePanel`; Playwright test with mock mode.
- [x] **T7 — MCP server.** stdio and HTTP transports, bearer token and rate limits; in-memory client test.
- [x] **T8 — Manual MCP check.** Connect from Claude Code and run three questions with tools that call no model; notes in Results.
- [x] **T9 — Injection gate (build gate).** The injection suite in agent mode end to end through the runner with scripted stand-ins: the obedient one is caught (planted text and a tool call outside the snapshot), the careful one scores zero leaks over three runs. The live run is a live-pass item (decision 0022).
- [x] **T10 — Evaluation (tooling).** `agent` and `oneshot` through the runner on the same questions with stand-ins, compared per question type with 95% intervals and cost per question; the agent conversation's request shapes replayed from a cassette. The test-split report is a live-pass item (decision 0022).

## Exit criteria

- [x] Tool, loop, and cap tests pass with the scripted fake LLM.
- [x] The injection suite runs end to end in agent mode without a model: planted text reaches the agent's prompts marked, the scorer catches a stand-in that obeys it (in its answer and in a tool call outside the snapshot), and a stand-in that answers from the evidence scores zero leaks over three runs.
- [x] The MCP in-memory client test passes, and the manual check is recorded (done from a Claude Code session through the SDK's stdio client; connecting Claude Code's own MCP client is an owner step, see Results).
- [x] `agent` and `oneshot` run through the eval runner on the same questions without a model, and the comparison reports the difference per question type with 95% intervals and the cost per question.

### Live pass (pending; not part of this gate)

These need spending the owner has deferred (decisions 0020 and 0022). They are not checked here and must never be reported as passed until they are run:

- Live injection suite: zero canary leaks in agent mode, three runs per question.
- The agent-versus-one-shot report on the test split, three runs each, with 95% intervals and cost per question.
- The agent cassette re-recorded from the API.
- `max_steps` and the token budgets checked on the dev split.
- A question answered in agent mode in the running app by the live model.

## Verification

```bash
cd backend
uv run pytest tests/unit/agent tests/integration/agent tests/integration/mcp -q
uv run pytest -m injection_static -q
uv run repoatlas mcp --snapshot <id> --transport stdio
```

Live pass (needs an API key; costs money):

```bash
uv run repoatlas eval run --suite injection --system agent --runs 3 --max-cost 3.00
uv run repoatlas eval run --system agent --split test --runs 3 --max-cost 25.00
```

## Risks and open questions

- Agents can loop or wander; step caps, the repeated-call guard, and budgets bound the cost.
- Model behavior for tool use may change; recorded tests catch regressions.
- Open: the right `max_steps` and per-tool budgets; tuned on dev.

## Results

**Build gate reached on 2026-11-15 (decision 0022). No model was called and nothing was spent.**
Evidence: [`docs/m14-gate-evidence.md`](../m14-gate-evidence.md).
CI run 43662452645 passed all five jobs on `d692d33` (Linux full checks, Windows unit tests, API
types, frontend with end-to-end tests, docs lint).

What was built:

- **Eight read-only tools** over one snapshot (`repoatlas.agent.tools`), with flat schemas,
  arguments checked before anything runs, and evidence references on every result.
- **Tool output prepared for a model** (`repoatlas.agent.guard`): trimmed to a token budget with
  a note on what was cut, passed through the injection detector, and followed by an evidence
  list that repository text can't forge.
- **The agent loop** (`repoatlas.agent.loop`): exploration on the `small` model with a step cap
  (12), a token budget (200,000 input tokens per answer), the answer's cost budget, a
  repeated-call guard, and tool errors returned to the model; an evidence ledger with stable
  IDs; the final answer on the `large` model over the same conversation, validated by the same
  `CitationValidator` and refused by the same rules as one-shot answers.
- **Agent mode in the API and the web app**: `mode: "agent"` on the ask endpoint, `step` events
  that add up to the answer's cost and link to spans of its trace, a mode toggle, and live steps
  in the ask panel; mock mode serves the same events.
- **The MCP server** (`repoatlas mcp`): the same tools over stdio and Streamable HTTP, with a
  required bearer token and a per-client rate limit on HTTP; documented in `docs/mcp.md`.
- **For the live pass**: the `agent` answer system in the eval runner, the injection suite for
  agent mode, `eval compare` with cost per question, the offline stand-in playing an agent, and
  a cassette of a whole agent conversation with its recording test.

Numbers of the gate (2,127 passed, 1 skipped, 8 deselected in `poe check`):

| Check | Result |
|---|---|
| Tool, loop, cap, ledger, and answer tests with a scripted model | Pass (155 unit tests of the tools, guard, ledger, loop, answer system, and MCP gate; 24 on a built snapshot; 14 for MCP over its client and transports) |
| Injection suite in agent mode, obedient stand-in | Caught on all 16 indexed variants and 5 markers; the planted read of `/etc/passwd` was made in 10 conversations, refused all 10 times, and counted as a tool leak each time; score 0.000 |
| Injection suite in agent mode, careful stand-in | 0 leaks in 45 answers and 291 tool calls over three runs; score 1.000 |
| MCP | 30 tests: in-memory client, a stdio subprocess, HTTP with token and rate limit; manual check below |
| Agent versus one-shot through the runner | Both systems on the same six questions, three runs each; `eval compare` reports the difference per type with 95% intervals and cost per question (stand-in numbers, not results) |
| Frontend | Lint and typecheck clean; 325 unit tests; Playwright 22 of 22 |

What the gate does not show, and nothing here claims:

- **Whether the real model resists the planted instructions in agent mode** (live pass item 12).
  The stand-ins are scripts. Agent mode is not deployed until that run scores 1.000.
- **Whether the agent beats one-shot retrieval on multi-hop questions, and at what cost** (item
  13). The comparison table in the evidence file measures two scripts.
- **Whether the defaults are right** (`max_steps` 12, the token budgets): they are reasoned, not
  tuned on dev (item 11).
- **Whether the API accepts the agent's requests as sent.** The request rules follow decision
  0010 and the current documentation, and a hand-written cassette pins the shapes, but the
  first real tool-use conversation through LiteLLM is item 10. Two things to watch there: the
  final call sends a response schema together with the tools and tool choice `none`, and it
  sends the `small` model's thinking blocks to the `large` model (the API is documented to drop
  blocks a model can't read).

Found on the way:

- The MCP SDK's current major version is 2 (2.3.0), whose API differs from 1.x: handlers are
  constructor arguments, and `Client(server)` connects in-process. Ping is gone from the modern
  protocol.
- Numbered code (`12 | …`) hid an instruction split over two comment lines from the detector,
  because the gutter sits between the lines. Tool output is now scanned with the gutters
  blanked; the fixture's split variant is the test.
- `eval compare` did not show cost. It now reports each system's cost per question per run, per
  type and overall.

### Manual MCP check (T8, 2026-11-15)

Done from the Claude Code session that built this milestone, against the real stdio server
(`repoatlas mcp --snapshot 20`, the benchmark snapshot of `psf/requests` at `6e83187b8feb` in
the local database, with the real embedding model). The session chose each call from the
previous result, the way an agent would, and sent it through the MCP SDK's stdio client
(`scripts/mcp_check.py`). The server called no model, and nothing was spent.

**How it differs from the plan.** The plan was to connect Claude Code's own MCP client. That
needs the server registered in the owner's Claude Code configuration, which is the owner's step,
and a headless `claude -p --mcp-config <file>` run could not authenticate on this machine (its
login had expired; it made no call). So the protocol, the transport, and the tools were checked
with the SDK's client instead, and the check through Claude Code's client is left as an owner
step that costs nothing:

```bash
claude mcp add repoatlas -- uv run --directory backend repoatlas mcp --snapshot 20
```

Then ask Claude Code the three questions below and compare.

| Question | Calls | What came back |
|---|---|---|
| Where is `Session.send` defined, and what does it call? | `find_definition`, `find_callees`, `read_file` | `src/requests/sessions.py` lines 752-829; 12 callees, among them `Session.get_adapter`, `HTTPAdapter.send`, `SessionRedirectMixin.resolve_redirects`, `dispatch_hook`, `extract_cookies_to_jar`; lines 752-775 read back with line numbers |
| Who calls `get_adapter`? | `find_definition`, `find_callers` | `Session.send` and six tests in `tests/test_requests.py` (the `test_session_get_adapter_prefix_*` tests and `_patch_adapter_gzipped_redirect`), each with its call site as evidence |
| What could break if `merge_setting` changes? | `find_definition`, `get_impact` | 19 affected symbols (8 listed): `Session.prepare_request` (0.46), `Session.merge_environment_settings` (0.38), `merge_hooks`, the files importing `sessions.py`, and `tests/test_requests.py` |

Also checked in the same session:

- `search_code` ("how are redirects followed and limited", k = 3) returned `TooManyRedirects`
  and the two redirect-limit tests in 4.7 s, most of it loading the embedding model; every
  graph call took under 0.4 s.
- `get_owners` for `src/requests/sessions.py` returned four authors with shares.
- `read_file` with the path `../../.env` was refused as an error result ("is not a file of this
  snapshot: the path has an empty, '.', or '..' segment"); nothing outside the snapshot can be
  read.
- Every result ended with its evidence list, with compact evidence references as IDs
  (`src/requests/sessions.py#L76-L105@feaf340`); no result of this repository was flagged.
- One thing to know when using it: `find_definition` with a dotted name returns the exact match
  first, followed by fuzzy matches up to its limit of ten.
