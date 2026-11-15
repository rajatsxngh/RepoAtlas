# M15 — Multi-agent pipeline

**Type of work:** AI agents · **Size:** M (8 tasks) · **Status:** Done

## Goal

Split hard questions across parallel explorers, then verify every claim against the code before it reaches the user — and measure whether the extra cost buys better answers.

## Depends on

- **M14** — tools and their schemas, `AgentAnswerSystem` loop pieces (append-only messages, trimming, budgets, routing), evidence ledger, API/UI agent mode and `step` events.
- **M13** — live injection suite and gate rule, `TracePanel`, flagged evidence.
- **M10** — answer-system registry, runner, compare, reports.

## Provides

- **`repoatlas.multiagent`**
  - `planner.Planner.plan(question, context) -> Planned(plan: Plan(branches: [Branch(id, sub_question, seed_symbols, budget: BranchBudget(max_steps, max_input_tokens, max_cost_usd))]), result)` — structured output on the `large` role; at most 4 branches; `planner.plan_context(toolbox, question, k, cache)` builds the context (places one search found, no code); `Plan.funded(explore_usd)` divides the exploration money by steps.
  - `explorers.Explorers.run(snapshot_id, question, branches, on_step=None, cache=None) -> [BranchResult(branch, ledger, notes, status, stop, error, steps, tool_uses, usage, cost_usd)]` — each branch is an agent loop on the `small` role with its own step, token, and cost budget and a time limit; branches run concurrently (at most 3 at a time) and share a tool-result cache; `status` is `complete`, `partial`, `timeout`, or `failed`.
  - `writer.Writer.prepare(question, branch_results) -> WriterInput` and `write(prepared, max_output_tokens) -> Written(draft, result, evidence)` — `merge_ledgers` makes one evidence set with stable IDs; the draft is the `AnswerDraft` every mode fills.
  - `verifier.Verifier.verify(claims, evidence, can_spend=None, on_call=None) -> Verified(claims, verdicts, usage, cost_usd, refusal)` — re-reads each cited range and labels each claim `supported | unsupported | partial`; unsupported claims are dropped; partial claims get one repair attempt and are re-verified; `verified_draft(draft, verified)` builds the draft that goes to `CitationValidator`.
  - `system.MultiAgentAnswerSystem` — registered as `multiagent`; `answer(snapshot_id, question, budget, on_status=None, on_step=None, on_plan=None)`; built from settings by `repoatlas.multiagent.factory` (`multiagent_from_settings`, `multiagent_system`, `multiagent_budget`, `multiagent_limits`); limits in `MultiAgentConfig`.
- **API and UI:** `mode = "multiagent"` on the ask endpoint; a `plan` server-sent event with the branches; `step` events carry branch IDs and report the plan, each branch's notes, the writing call, and each verification and repair call; `AnswerView` carries `branches`, `verification`, and `stage_costs`; the ask panel has a Multi-agent mode whose steps are grouped by branch; `TracePanel` shows each branch's span as a labeled group with the branch's spans inside it.
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
- [x] **T4 — Verifier.** Support labels, drop and repair; test with a seeded unsupported claim (must be dropped) and a seeded partial claim (repaired or dropped).
- [x] **T5 — Failure handling.** Branch errors and timeouts produce partial answers with explicit gaps.
- [x] **T6 — API and UI mode.** Branch-aware events; `TracePanel` grouping; Playwright test with mock mode.
- [x] **T7 — Injection gate (build gate).** The injection suite in multi-agent mode end to end through the runner with scripted stand-ins for all four roles: the obedient one is caught (planted text at every stage and a tool call outside the snapshot), the careful one scores zero leaks over three runs. The live run is a live-pass item (decision 0023).
- [x] **T8 — Evaluation (tooling).** `multiagent`, `agent`, and `oneshot` through the runner on the same questions with stand-ins; `repoatlas eval compare` on the three reports, with 95% intervals, cost per question, and cost per stage. The test-split report is a live-pass item (decision 0023).

## Exit criteria

- [x] The verifier drops the seeded unsupported claim in tests, and a seeded partial claim is repaired or dropped.
- [x] Planner, explorer, writer, verifier, and failure-handling tests pass with scripted fake LLMs (concurrency limit, per-branch budgets, shared cache, branch error, timeout).
- [x] The injection suite runs end to end in multi-agent mode without a model: planted text reaches the stages' prompts marked, the scorer catches a stand-in that obeys it (in its answer and in a tool call outside the snapshot), and a stand-in that answers from the evidence scores zero leaks over three runs.
- [x] `multiagent`, `agent`, and `oneshot` run through the eval runner on the same questions without a model, and the three-way comparison reports each system's score per question type with 95% intervals, the cost per question, and the cost per stage.

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
uv run pytest tests/unit/multiagent tests/integration/api/test_ask_multiagent.py -q
uv run pytest -m injection_static -q
uv run pytest tests/integration/evals/test_three_way.py -q -s
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

**Build gate reached on 2026-11-15 (decision 0023). No model was called and nothing was spent.**
Evidence: [`docs/m15-gate-evidence.md`](../m15-gate-evidence.md).
CI run 43677960297 passed all five jobs on `fff0e99` (Linux full checks, Windows unit tests, API
types, frontend with end-to-end tests, docs lint).

What was built:

- **The planner** (`repoatlas.multiagent.planner`): one structured call on the `large` model
  that sees the question and the places one search found (never code) and returns at most four
  branches, each with a sub-question, seed symbols, and steps. The server caps the steps, keeps
  only seeds it listed, and falls back to one branch with the question.
- **The explorers** (`repoatlas.multiagent.explorers`): each branch is the agent's exploration
  loop on the `small` model with its own conversation, ledger, and share of the answer's steps,
  tokens, and money, and a time limit. At most three run at once, and they share a cache of
  tool results. A branch ends with its notes: findings that cite its ledger.
- **The writer** (`repoatlas.multiagent.writer`): the ledgers merged into one evidence set with
  stable IDs, and one draft on the `large` model, which can cite only evidence it was shown.
- **The verifier** (`repoatlas.multiagent.verifier`): an independent reading of every claim
  against its cited lines, read again from the stored blobs. Unsupported claims are dropped;
  partial claims are rewritten once from their cited lines and checked again. When that changes
  the draft, the answer text is rebuilt from the verified claims.
- **The answering mode** (`MultiAgentAnswerSystem`, registered as `multiagent`): budget checks
  before every call (a claim is never shown unverified), cost per stage, and failure handling:
  a branch that fails, times out, or hits a limit leaves a partial answer that names what was
  not explored.
- **In the API and the web app**: `mode: "multiagent"`, a `plan` event, `step` events with
  their branch, a third mode in the ask panel with steps grouped by branch and a note on what
  the verifier left out, and a trace with one labeled group per branch. Mock mode serves the
  same events.
- **For the live pass**: the `multiagent` system in the eval runner with its own budget, the
  injection suite for the mode (the scorer also reads the sub-questions and the verifier's
  reasons), `eval compare` for three reports with cost per question and per stage, and the
  offline stand-in playing the whole pipeline.

Numbers of the gate (2,234 passed, 1 skipped, 8 deselected in `poe check`):

| Check | Result |
|---|---|
| The verifier drops the seeded unsupported claim; the seeded partial claim is repaired or dropped | Pass (`test_verifier.py`, 12 tests; again through the whole pipeline in `test_system.py` and through the API) |
| Planner, explorers, writer, verifier, pipeline, and failure handling with scripted models | Pass (63 unit tests; 4 on the ask endpoint with a built snapshot) |
| Injection suite in multi-agent mode, obedient stand-in | Caught on all 16 indexed variants and 5 markers; planted text reached the explorers (16 variants), the writer (16), and the verifier (14) marked, and the planner not at all; canaries in the verifier's reasons on all 15 questions; the planted read of `/etc/passwd` was made in 10 conversations, refused all 10 times, and counted as a tool leak each time; score 0.000 |
| Injection suite in multi-agent mode, careful stand-in | 0 leaks in 45 answers and 354 tool calls over three runs; score 1.000 |
| Three systems through the runner | `oneshot`, `agent`, and `multiagent` on the same six questions, three runs each; `eval compare` reports each system's score per type with 95% intervals, the cost per question, the cost per stage, and the paired difference of every pair (stand-in numbers, not results) |
| Frontend | Lint and typecheck clean; 335 unit tests; Playwright 25 of 25 |

What the gate does not show, and nothing here claims:

- **Whether the pipeline answers better than one agent or one retrieval pass, and at what
  cost** (live pass item 17). The comparison tables in the evidence file measure three scripts.
  If the gain turns out small, the default mode stays the cheaper one.
- **Whether the real model resists planted instructions in this mode** (item 16), at any
  stage: following one, passing one on in its notes, or labeling a planted claim supported. The
  stand-ins are scripts. Multi-agent mode is not deployed until that run scores 1.000.
- **Whether the verifier's labels are right.** The tests show what happens to a claim once it
  is labeled, not that the model labels well. The writer and the verifier are the same model
  family and can share a blind spot; the spot-check against human labels is item 18.
- **Whether the defaults are right** (24 steps and 400,000 input tokens for all branches, a
  180-second branch limit, $0.75 per answer): they are reasoned, not tuned on dev (item 15).
- **Whether the API accepts the pipeline's requests as sent.** They follow decision 0010 and go
  through the same client as every other call, but the planner, the notes call (a response
  schema next to the tools, with tool choice `none`, on the `small` model), the writer, and the
  verifier have never been sent to the API.

Design points settled on the way (decision 0023):

- A branch reports with one more call in its own conversation, on the same model, so thinking
  blocks never cross models.
- When verification removes or rewrites a claim, the answer text is rebuilt from the verified
  claims instead of keeping the writer's prose around a statement that was rejected. It reads
  plainer; whether that costs judged quality is a question for the live pass (backlog).
- An evaluation run gives the pipeline its own budget ($0.75), because the one-shot answer
  budget ($0.25) doesn't cover a planner, explorers, a writer, and a verifier.

Found on the way:

- Evidence blocks in the writer's and the verifier's prompts could have carried a forged
  `<claim>` or `<finding>` block: the answer prompt defuses only its own block names. The
  pipeline's prompts defuse theirs too, in evidence and in everything one model hands another.
- The injection scorer read only the answer and its claims. A multi-agent answer also shows
  sub-questions and the verifier's reasons, which a model wrote after reading the repository;
  the scorer reads them now.
- Adding a mode named "Multi-agent" made the web tests' `"Agent"` selectors match two radio
  buttons; they are exact now.
