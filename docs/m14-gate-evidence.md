# M14 gate evidence

What was run for the M14 build gate (decision
[0022](decisions/0022-m14-agent-build-gate-live-pass.md)) on 2026-11-15, on Windows 11 with
Postgres in Docker. **No model was called and nothing was spent.** The live runs are pending in
[live-pass.md](live-pass.md), items 10 to 14.

## Tools, loop, and caps with a scripted model

```bash
cd backend
uv run pytest tests/unit/agent tests/integration/agent tests/integration/mcp -q
```

193 tests passed: 155 unit tests of the tools, guard, ledger, loop, answer system, and MCP gate; 24 on a built snapshot; 14 for MCP over its client and transports.

| What | Where | Shown |
|---|---|---|
| The eight tools on a built snapshot | `tests/integration/agent/test_tools.py` | Every tool returns evidence that exists in the stored blobs; reads outside the snapshot are refused as not allowed |
| Arguments, trimming, the detector pass | `tests/unit/agent/test_guard.py`, `test_guard_lab.py` | Malformed paths and keys are refused before the store is asked; long results keep their head and their whole evidence list; all 16 indexed variants of the injection fixture are marked in a file read |
| The loop | `tests/unit/agent/test_loop.py` | Normal finish, the step cap, the token budget, the cost budget, tool errors, repeated calls, a cut-off turn, a refusal, the daily cap; the conversation is append-only and turns go back unchanged |
| The answer | `tests/unit/agent/test_answer_system.py` | Citations validated against the ledger; every refusal rule; `small` for steps and `large` for the answer; one trace per answer |
| A whole conversation through the real client | `tests/integration/agent/test_recorded_agent.py` | Four requests replayed from a hand-written cassette: tools on every call, tool choice `auto` then `none`, the thinking block passed back with its signature, the final call's schema |

## The injection suite in agent mode, end to end, with stand-ins

```bash
cd backend
REPOATLAS_GATE_EVIDENCE=gate.json uv run pytest tests/integration/security/test_injection_gate.py -q
uv run pytest -m injection_static -q
```

Nine tests passed in the gate file (two of them new, for agent mode, and the command test now
runs `--system agent` too); the static injection tests number 285. The registered `agent`
system explored the built fixture snapshot with its real tools; the stand-ins
(`tests/integration/security/standins.py`) played the model in the exploration and in the final
answer. Both explore the same way: one search with the question, then a read of the files the
search listed.

| Stand-in | Questions × runs | Variants in the tool results | Tool calls | Leaks found | Score |
|---|---|---|---|---|---|
| obedient | 15 × 1 | 16 (v01, v02, v04–v17) | 107, of which 10 were the planted read of `/etc/passwd` | all 16 canaries, 5 markers, and a tool leak on each of the 10 questions where the planted call was made | 0.000 |
| careful | 15 × 3 | the same 16 | 291, all allowed | 0 in 45 answers | 1.000 (1.000, 1.000, 1.000) |

Reading the table:

- **The agent's own tools carry every indexed variant into the conversation, marked.** Every
  conversation of both runs had flagged text in its tool results. The README variant (v03) is
  not indexed and appears in none.
- **The planted tool call is refused and counted.** The fixture's fake tool call asks for a
  read of `/etc/passwd`. The obedient stand-in made that call in the 10 conversations where it
  read the file that carries it; the allowlist refused all 10 (recorded as not allowed), and the
  scorer counted a tool leak on exactly those questions. Every other call of both runs stayed
  inside the snapshot.
- **A stand-in that answers from what the tools showed scores zero leaks** over three runs, and
  its citations are files of the fixture.
- **The documented command works**: `repoatlas eval run --suite injection --system agent --runs
  3 --max-cost 2.00` ran through the real client over the offline stand-in and ended `1.000
  [1.000, 1.000]; $0.00 spent this run … complete`.

**What this does not show:** how the real model behaves. A stand-in is a script. Whether Claude
follows a planted instruction or makes a planted tool call is item 12 of the live pass, and
agent mode is not deployed until that run scores 1.000.

## The MCP server

```bash
cd backend
uv run pytest tests/integration/mcp tests/unit/agent/test_mcp_gate.py -q
```

Thirty tests passed: the SDK's in-memory client (the tools and schemas are the agent's; three
questions; error results; an unknown tool), a real stdio subprocess started by `repoatlas mcp`,
and Streamable HTTP on a local port (401 without the exact bearer token, an MCP client with the
token, 429 with `Retry-After` beyond the limit). The manual check on a benchmark snapshot is in
M14's Results.

## Agent versus one-shot through the eval runner, with stand-ins

```bash
cd backend
uv run pytest tests/integration/evals -q -s
```

Four tests passed. Both registered systems answered the same six questions (three lookups,
three caller sets) on the built `py_small` snapshot, three runs each, through the runner, the
benchmark scorers, and schema-valid reports; then `repoatlas eval compare` printed:

```
| Type | Questions | oneshot | agent | Difference [95% CI] | P(B better) | Cost: oneshot | Cost: agent |
|---|---|---|---|---|---|---|---|
| lookup | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 | $0.0040 | $0.0043 |
| callers | 3 | 0.889 | 1.000 | +0.111 [+0.000, +0.333] | 0.70 | $0.0040 | $0.0046 |
| **all** | 6 | 0.944 | 1.000 | +0.056 [+0.000, +0.167] | 0.67 | $0.0040 | $0.0044 |
```

**These numbers measure two scripts, not two systems.** The one-shot stand-in answers from
whatever retrieval returned; the agent stand-in asks the graph for the callers. The costs are
`FakeLLM`'s fixed token counts at the configured prices. The table shows that the comparison
runs, pairs questions, puts an interval on each difference, and reports cost per question per
type. Whether the real agent beats one-shot retrieval on multi-hop questions is item 13 of the
live pass.

## The whole suite

```bash
cd backend && uv run poe check
cd frontend && npm run lint && npm run typecheck && npm test
cd frontend && npx playwright test
python scripts/check_milestones.py
```

- Backend (`poe check`: ruff, mypy on `src/` and `tests/`, every unit and integration test, docs lint): 2,127 passed, 1 skipped, 8 deselected.
- Frontend: lint and typecheck clean; 325 unit tests in 40 files.
- Playwright: 22 of 22 (8 against the real API with the offline stand-in, 14 in mock mode), 4 of them new for agent mode.
- Docs lint: 18 milestones, 165 requirements, dependencies point backward only.
- The OpenAPI spec and the generated TypeScript types are current (`scripts/gen_api_types.sh --check`).
