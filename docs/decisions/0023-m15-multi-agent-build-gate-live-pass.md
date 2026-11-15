# 0023 — M15 without spend: the multi-agent build gate, and what moved

**Status:** Accepted · **Date:** 2026-11-15

## Context

M15 was planned with two checks that call the model: the live injection suite in multi-agent
mode (zero leaks), and the three-way report on the test split (multi-agent, agent, one-shot,
with 95% intervals and cost per question and per stage). Decision 0020 says the build spends
nothing, so neither can run now. This record applies the replan M12, M13, and M14 used; nothing
about the product changes.

The milestone file left several design points open (how a branch reports what it found, what
happens to the answer text when the verifier removes a claim, how budgets are split). They are
settled here, together with the additions to contracts of earlier milestones.

## Decision

1. **The product is unchanged by the constraint.** The planner, the explorers, the writer, the
   verifier, failure handling, and multi-agent mode in the API and the web app are built as
   designed. In a deployment every stage calls Claude through `LLMClient` (decision 0010):
   `large` for the planner, the writer, and the verifier; `small` for the explorers.
2. **The M15 gate is a build gate.** Everything is tested without a model:
   - each stage and the whole pipeline with scripted `FakeLLM`s, including the test in which
     the verifier drops a seeded unsupported claim, and the one in which a seeded partial claim
     is repaired or dropped;
   - concurrency limits, per-branch budgets, the shared tool cache, branch errors, and
     timeouts;
   - the injection suite in multi-agent mode, end to end through the eval runner on the built
     fixture snapshot, with two scripted stand-ins that play all four roles. The *obedient* one
     follows every planted instruction it reads, at every stage, and the scorer must catch it.
     The *careful* one must score zero leaks over three runs. As in decisions 0021 and 0022,
     this proves that planted text reaches each stage's prompt marked, that the allowlist
     refuses a planted tool call, and that the scorer and the command work. It says nothing
     about how the real model behaves;
   - the evaluation path: `multiagent`, `agent`, and `oneshot` run through the runner on the
     same questions with stand-ins, and `repoatlas eval compare` takes the three reports and
     prints every system's score per question type with 95% intervals, the paired differences,
     the cost per question, and the cost per stage. The numbers of such a run measure the
     stand-ins, not the systems, and are not reported as results.
3. **The live checks move to the live pass,** pending in [`docs/live-pass.md`](../live-pass.md)
   and in M15's file, never passed:
   - live injection suite in multi-agent mode: zero canary leaks, three runs per question;
   - the three-way report on the test split, with intervals, cost per question, and cost per
     stage;
   - the budgets below checked on the dev split (they are reasoned, not tuned);
   - the verifier's agreement with human labels, spot-checked on dev (a risk the milestone
     names);
   - a question answered in multi-agent mode in the running app by the live model.
   The gate rule of decision 0021 stands: multi-agent mode ships only with zero leaks on the
   live suite. Until then it is not deployed anywhere.
4. **Running them later is configuration plus one command each** (see the live-pass file).
5. **The offline stand-in learns to play the pipeline** (extends decisions 0020 and 0022). It
   plans one branch with the question, explores it as it explores in agent mode, reports one
   finding per evidence entry, drafts from the evidence blocks, and labels every claim
   `supported`. Usage stays zero. It exists so the documented commands and the end-to-end stack
   can exercise multi-agent mode without a key; it is still refused when a key is set. A
   stand-in "verdict" verifies nothing, and the answer text says no model wrote it.

### How the pipeline works (design)

- **Planner** (`large`, structured output). It sees the question and a *context*: the symbols
  and files one hybrid search for the question found (keys, kinds, paths, line ranges; no
  code), marked by the injection detector like any repository text. It returns at most four
  branches, each a sub-question, seed symbols, and a number of steps. The server keeps only
  seeds that the context listed, gives the branches the IDs `b1…`, and caps the steps. A plan
  with no usable branch becomes one branch with the question itself.
- **Budgets.** One answer has a cost budget, a step budget, and an input-token budget for all
  its branches together. The planner divides the steps; a branch's share of the tokens and of
  the exploration money follows its share of the steps. The money for exploring is what is
  left of the answer's budget after the planner's cost and a reserve for the writer and one
  verification pass. A branch that hits a limit stops, reports what it has, and is `partial`.
- **Explorers** (`small`). Each branch is the M14 `Explorer` loop with its own conversation,
  ledger, and limits. At most three run at a time. Identical tool calls are answered once for
  the whole answer (a cache shared by the branches); each branch still lists the evidence
  under its own ledger IDs. A branch ends with one more call in the same conversation, on the
  same model, that returns its notes: findings that cite its ledger IDs, and what it could not
  establish. (Same model, same conversation: thinking blocks never cross models here.)
- **Writer** (`large`, structured output). The ledgers are merged into one evidence set:
  branches in plan order, entries in ledger order, the same lines counted once, numbered
  `E1…`. The writer sees the question, the evidence (cited by findings first, then the rest,
  up to `answer_evidence_tokens`), each branch's findings with their IDs rewritten to the
  merged ones, and which branches did not finish. It fills the same `AnswerDraft` as the other
  modes. It can cite only evidence it was shown.
- **Verifier** (`large`, structured output). It sees only the claims and the cited lines,
  read again from the stored blobs: not the question's exploration, the notes, or the writer's
  prose. It labels each claim `supported`, `partial`, or `unsupported`. Unsupported claims are
  dropped. Partial claims are rewritten once (one call, from their cited lines only; a rewrite
  may drop citations, never add one) and verified again (one call); what is not `supported`
  then is dropped. A claim the verifier gives no label is treated as unsupported.
- **The answer text.** When verification removed or rewrote a claim, the writer's prose around
  it can no longer be trusted, so the answer text is rebuilt from the verified claims, one line
  per claim with its marker. When every claim was supported as written, the writer's text is
  kept. Either way the result passes `CitationValidator`, and the labels are stored with the
  answer.
- **Failures.** A branch that raises, is refused by the API, or passes its time limit does not
  fail the answer: what its ledger holds is still used, and the answer ends with a list of the
  sub-questions that were not fully explored and why. If every branch failed with an error and
  nothing was found, the first error is raised (the API reports it as it reports any model
  failure). If nothing was found for another reason, the answer is a refusal of the usual
  kinds.
- **When there is no money left to verify.** A claim is not shown unverified: if the
  verification call does not fit the budget, the answer is a `budget` refusal. If only the
  repair does not fit, partial claims are dropped.

### Multi-agent defaults (untuned)

| Setting | Default | Why |
|---|---|---|
| `multiagent_max_branches` | 4 | The planned cap. |
| `multiagent_concurrency` | 3 | The planned cap on branches running at once. |
| `multiagent_total_steps` | 24 | Twice the agent's 12, so two full branches or four short ones; a branch gets 2 to 12. |
| `multiagent_max_input_tokens` | 400,000 | Twice the agent's token budget, for all branches together. |
| `multiagent_branch_timeout_s` | 180 | A branch of twelve steps takes well under this; it stops one that hangs. |
| `multiagent_max_cost_usd` | 0.75 | Worst case by the price table: planner about $0.03, explorers about $0.08, writer $0.13, verification $0.39 (three calls, each with its repair attempt). Typical answers should cost far less. |

### Contract additions in earlier milestones

All are backward compatible; the milestone files list them under "Added later by decision
0023".

- **Answer contracts (M07).** `AnswerResult` gains `branches: list[BranchSummary]`,
  `verification: list[ClaimVerdict]`, and `stage_costs: dict[str, float]` (all empty for the
  other modes). `ToolUse` gains `branch` (the branch that made the call; `None` elsewhere).
- **Agent loop (M14).** `Explorer` takes a `closing` text function and a shared `ToolCache`;
  its work is split into `begin` and `explore` so a caller keeps the state of a branch it had
  to cancel (`run` is unchanged). `AgentConfig` gains `notes_output_tokens`. `StepEvent` gains
  `branch` and the kinds `plan`, `notes`, `write`, `verify`, and `repair`.
- **Ask API (M04, M07).** `mode` accepts `multiagent`; the stream gains a `plan` event (the
  branches) and `step` events carry `branch`. A stored run has mode `multiagent`.
  `AnswerView` gains the three `AnswerResult` fields.
- **Eval runner (M10).** The answer system `multiagent` is registered. An answer system may
  name its own budget (`default_budget`), which an evaluation run then uses instead of
  `answer_max_cost_usd`. `SystemOutput` and the report's runs gain `stage_costs`.
  `repoatlas eval compare` accepts more than two reports: it prints a summary of every system
  and the paired comparison of each pair, and a cost-per-stage table for systems that report
  stages.
- **Offline stand-in (M07, decision 0020).** As described in item 5.
- **Settings.** `multiagent_*` above.

### "Including impact explanation questions"

The benchmark's `impact` questions ask what a change affects; an answer system answers them
with a cited explanation. The three-way report covers every question type of the test split,
`impact` among them, which is what MAS-7 asks for. The separate `impact_explain` system (the
explanation of a stored impact run) is not an answering mode a user picks and is not part of
the comparison.

## Consequences

- M15 closes as Done on its build criteria with five live-pass items open.
- Whether the pipeline's extra cost buys better answers is unknown until the live pass. Nothing
  in the docs or the app claims it. If the gain is small, the default mode stays the cheaper
  one, as the milestone says.
- Rebuilding the answer text from claims makes a verified answer plainer than the writer's
  prose. It is the price of never showing a statement the verifier rejected. If the live pass
  shows it hurts the judged quality of answers, the alternative (asking the writer to revise
  its text after verification, one more `large` call) goes to the backlog with the numbers.
- The verifier and the writer are the same model family, so they can share a blind spot. The
  build gate cannot measure that; the spot-check against human labels is a live-pass item.
- A fourth offline script to keep working; a unit test and the injection gate's command test
  cover it.
