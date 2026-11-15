# M15 — Multi-agent pipeline

**Type of work:** AI agents · **Size:** M (8 tasks) · **Status:** In progress

## Goal

Split hard questions across parallel explorers, then verify every claim against the code before it reaches the user — and measure whether the extra cost buys better answers.

## Depends on

- **M14** — tools and their schemas, `AgentAnswerSystem` loop pieces (append-only messages, trimming, budgets, routing), evidence ledger, API/UI agent mode and `step` events.
- **M13** — live injection suite and gate rule, `TracePanel`, flagged evidence.
- **M10** — answer-system registry, runner, compare, reports.

## Provides

- **`repoatlas.multiagent`**
  - `planner.plan(question, context) -> Plan(branches: [Branch(id, sub_question, seed_symbols, budget)])` — structured output on the `large` role; at most 4 branches.
  - `explorers.run(branches) -> [BranchResult(ledger, notes, status)]` — each branch is an agent loop on the `small` role with its own step and token budget; branches run concurrently (at most 3 at a time) and share a tool-result cache.
  - `writer.write(question, branch_results) -> AnswerDraft` — merges ledgers into one evidence set and drafts claims.
  - `verifier.verify(draft, evidence) -> VerifiedDraft` — re-reads each cited range and labels each claim `supported | unsupported | partial`; unsupported claims are dropped; partial claims get one repair attempt and are re-verified.
  - `MultiAgentAnswerSystem` — registered as `multiagent`.
- **API and UI:** `mode = "multiagent"` on the ask endpoint; server-sent events carry branch IDs; `TracePanel` groups spans by branch.
- **Results:** the injection suite and the three-way comparison run end to end without a model (decision 0023); the live results are pending in [`docs/live-pass.md`](../live-pass.md).

## Requirements

MAS-1, MAS-2, MAS-3, MAS-4, MAS-5, MAS-6, MAS-7

## Scope

**In:** planner, parallel explorers with budgets and a shared cache, writer, verifier with drop and repair, failure handling, API/UI mode, injection gate, evaluation.

**Out:** changes to tools, cost tuning beyond per-branch budgets.

## Design notes

- **When the planner adds value:** for simple lookups the plan may have a single branch; the planner is told to prefer one branch unless the question has independent parts.
- **Budgets:** the total budget is split across branches (planner decides, capped); a branch that hits its budget returns its ledger with `status = "partial"`.
- **Failure handling:** a branch error or timeout doesn't fail the request; the writer receives the surviving branches and the answer states which parts couldn't be explored.
- **Verification:** the verifier sees only the claim and the exact cited code (not the explorers' reasoning), which makes it an independent check; its labels are stored with the answer; the final answer still passes `CitationValidator`.
- **Repairs:** a partial claim is rewritten once by the `large` model using only its cited code, then re-verified; still-unsupported claims are dropped.
- **Cost accounting:** spans per branch, planner, writer, and verifier; the report breaks cost down by stage.

## Tasks

- [x] **T1 — Planner.** Structured plan schema; tests with `FakeLLM` (single-branch and multi-branch questions).
- [x] **T2 — Explorers.** Concurrent branches, per-branch budgets, shared tool cache; tests for concurrency limits and partial results.
- [x] **T3 — Writer.** Ledger merging with stable evidence IDs; draft generation.
- [ ] **T4 — Verifier.** Support labels, drop and repair; test with a seeded unsupported claim (must be dropped) and a seeded partial claim (repaired or dropped).
- [ ] **T5 — Failure handling.** Branch errors and timeouts produce partial answers with explicit gaps.
- [ ] **T6 — API and UI mode.** Branch-aware events; `TracePanel` grouping; Playwright test with mock mode.
- [ ] **T7 — Injection gate (build gate).** The injection suite in multi-agent mode end to end through the runner with scripted stand-ins for all four roles: the obedient one is caught (planted text at every stage and a tool call outside the snapshot), the careful one scores zero leaks over three runs. The live run is a live-pass item (decision 0023).
- [ ] **T8 — Evaluation (tooling).** `multiagent`, `agent`, and `oneshot` through the runner on the same questions with stand-ins; `repoatlas eval compare` on the three reports, with 95% intervals, cost per question, and cost per stage. The test-split report is a live-pass item (decision 0023).

## Exit criteria

- [ ] The verifier drops the seeded unsupported claim in tests, and a seeded partial claim is repaired or dropped.
- [ ] Planner, explorer, writer, verifier, and failure-handling tests pass with scripted fake LLMs (concurrency limit, per-branch budgets, shared cache, branch error, timeout).
- [ ] The injection suite runs end to end in multi-agent mode without a model: planted text reaches the stages' prompts marked, the scorer catches a stand-in that obeys it (in its answer and in a tool call outside the snapshot), and a stand-in that answers from the evidence scores zero leaks over three runs.
- [ ] `multiagent`, `agent`, and `oneshot` run through the eval runner on the same questions without a model, and the three-way comparison reports each system's score per question type with 95% intervals, the cost per question, and the cost per stage.

### Live pass (pending; not part of this gate)

These need spending the owner has deferred (decisions 0020 and 0023). They are not checked here and must never be reported as passed until they are run:

- Live injection suite: zero canary leaks in multi-agent mode, three runs per question.
- The three-way report on the test split (multi-agent, agent, one-shot), three runs each, with 95% intervals, cost per question, and cost per stage.
- The step, token, and cost budgets checked on the dev split.
- The verifier's labels spot-checked against human labels on dev.
- A question answered in multi-agent mode in the running app by the live model.

## Verification

```bash
cd backend
uv run pytest tests/unit/multiagent tests/integration/multiagent -q
uv run pytest -m injection_static -q
uv run pytest tests/integration/evals/test_three_way.py -q
```

Live pass (needs an API key; costs money):

```bash
uv run repoatlas eval run --suite injection --system multiagent --runs 3 --max-cost 4.00
uv run repoatlas eval run --system multiagent --split test --runs 3 --max-cost 40.00
uv run repoatlas eval compare eval/reports/<oneshot>.json eval/reports/<agent>.json eval/reports/<multiagent>.json
```

## Risks and open questions

- Multi-agent pipelines are expensive; if accuracy gains are small, the report says so and the default mode stays cheaper.
- Verifier and writer share a model family, so the verifier can miss the same mistakes; its agreement with human labels is spot-checked on dev.
- Open: whether the verifier should run on agent-mode answers too (it can, behind the same interface).

## Results

_Filled in at the gate._
