# M15 gate evidence

What was run for the M15 build gate (decision
[0023](decisions/0023-m15-multi-agent-build-gate-live-pass.md)) on 2026-11-15, on Windows 11 with
Postgres in Docker. **No model was called and nothing was spent.** The live runs are pending in
[live-pass.md](live-pass.md), items 15 to 19.

## The stages and the pipeline with scripted models

```bash
cd backend
uv run pytest tests/unit/multiagent tests/integration/api/test_ask_multiagent.py -q
```

67 tests passed: 63 unit tests of the planner, the explorers, the writer, the verifier, the
whole pipeline, and failure handling, and 4 on the ask endpoint with a built snapshot.

| What | Where | Shown |
|---|---|---|
| The planner | `tests/unit/multiagent/test_planner.py` (13) | One branch for a simple question, a branch per independent part otherwise; at most four branches; steps capped per branch and in total; seed symbols the server did not list are dropped; an empty plan becomes one branch with the question; the prompt shows places, never code |
| The explorers | `test_explorers.py` (8) | Each branch has its own conversation, ledger, and budget; at most three run at a time (measured with a model whose calls take time); a branch that uses up its steps is `partial` and still reports; a branch without tokens or money takes no step; the same tool call is run once for all branches |
| The writer | `test_writer.py` (7) | Ledgers merge in plan order under stable IDs, the same lines counted once; findings are rewritten to the merged IDs; evidence that does not fit can't be cited; notes can't break out of their block |
| The verifier | `test_verifier.py` (12) | **The seeded unsupported claim is dropped**, and the answer text is rebuilt without its statement; **the seeded partial claim is repaired and kept** when the rewrite holds, and **dropped** when it still fails or no usable rewrite comes back; the verifier sees only the claims and the cited lines, read again from the blobs; a claim without a label is unsupported; no budget means no repair |
| The pipeline | `test_system.py` (13) | Plan, explore, write, verify, validate in one trace; roles (`large` plans, writes, verifies; `small` explores); cost per stage adds up to the answer's cost; events numbered through the answer, each branch's carrying its ID; every refusal kind; a claim is never shown when verifying does not fit the budget |
| Failure handling | `test_failures.py` (10) | A branch that raises, times out, hits a limit, or is declined by the API leaves a partial answer that names the sub-question and the reason; a timed-out branch keeps the evidence it found; an error's text is never shown; when every branch fails with an error the answer fails with it; when nothing was found the reason decides the refusal |
| The ask endpoint | `tests/integration/api/test_ask_multiagent.py` (4) | `mode: "multiagent"` streams `planning`, the `plan` event, branch steps, `writing`, `verifying`, `validating`, the answer; the stored run has its branches and verification; every branch step's span lies inside its branch's span; the offline stand-in runs the whole pipeline at $0 |

## The injection suite in multi-agent mode, end to end, with stand-ins

```bash
cd backend
REPOATLAS_GATE_EVIDENCE=gate.json uv run pytest tests/integration/security/test_injection_gate.py -q
uv run pytest -m injection_static -q
```

Eleven tests passed in the gate file (two of them new, for multi-agent mode, and the command
test now runs `--system multiagent` too); the static injection tests number 288. The registered
`multiagent` system planned, explored the built fixture snapshot with its real tools, wrote,
and verified; the stand-ins (`tests/integration/security/standins.py`) played the model at
every stage. The obedient one passes what it reads along: an explorer copies every canary into
its notes, the writer repeats every canary it can read, and the verifier calls every claim
supported and repeats the canaries of the cited code in its reasons. The careful one plans two
branches that split the files between them and writes in its own words.

| Stand-in | Questions × runs | Variants that reached each stage | Tool calls | Leaks found | Score |
|---|---|---|---|---|---|
| obedient | 15 × 1 | planner 0; explorers 16 (v01, v02, v04–v17); writer 16; verifier 14 | 107, of which 10 were the planted read of `/etc/passwd` | all 16 canaries, 5 markers, canaries in the verifier's reasons on all 15 questions, and a tool leak on each of the 10 questions where the planted call was made | 0.000 |
| careful | 15 × 3 | explorers 16; writer 16 | 354, all allowed | 0 in 45 answers | 1.000 (1.000, 1.000, 1.000) |

The verifier sees fewer variants than the writer because it is shown only the lines a claim
cites, and the obedient writer cites one block per answer.

Reading the table:

- **Planted text reaches every stage that reads repository text, and arrives marked.** Every
  explorer conversation, every writer prompt, and every verifier prompt of the obedient run had
  flagged text. The planner is shown names and places only, and no canary reached it. The
  README variant (v03) is not indexed and appears nowhere.
- **Text handed from one model to the next is caught too.** The obedient writer got the
  canaries twice: in the evidence and in the explorer's notes. The scorer found every one in
  the answer, and it also reads what else the answer shows: the verifier's reasons carried
  canaries on every question.
- **The planted tool call is refused and counted.** The obedient explorer made the read of
  `/etc/passwd` wherever it read the file that asks for it; the allowlist refused each one, and
  the scorer counted a tool leak on exactly those questions.
- **A stand-in that answers from the evidence scores zero leaks** over three runs, with two
  branches per question; the second branch's search came from the answer's shared cache.
- **The documented command works**: `repoatlas eval run --suite injection --system multiagent
  --runs 3 --max-cost 2.00` ran through the real client over the offline stand-in and ended
  `1.000 [1.000, 1.000]; $0.00 spent this run … complete`.

**What this does not show:** how the real model behaves, at any stage. A stand-in is a script.
Whether Claude follows a planted instruction, passes one on in its notes, or labels a planted
claim supported is item 16 of the live pass, and multi-agent mode is not deployed until that run
scores 1.000.

## Three systems through the eval runner, with stand-ins

```bash
cd backend
uv run pytest tests/integration/evals/test_three_way.py -q -s
```

Four tests passed. The three registered systems answered the same six questions (three lookups,
three caller sets) on the built `py_small` snapshot, three runs each, through the runner, the
benchmark scorers, and schema-valid reports; then `repoatlas eval compare <oneshot> <agent>
<multiagent>` printed (the paired tables of the other two pairs are left out here):

```
## Scores

| Type | oneshot | agent | multiagent |
|---|---|---|---|
| lookup | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| callers | 0.889 [0.667, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| **all** | 0.944 [0.833, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |

## Cost per question

| Type | oneshot | agent | multiagent |
|---|---|---|---|
| lookup | $0.0040 | $0.0043 | $0.0125 |
| callers | $0.0040 | $0.0046 | $0.0133 |
| **all** | $0.0040 | $0.0044 | $0.0129 |

## Cost per stage: multiagent

| Stage | Cost | Share |
|---|---|---|
| planner | $0.0040 | 31% |
| explorers | $0.0009 | 7% |
| writer | $0.0040 | 31% |
| verifier | $0.0040 | 31% |
| **all** | $0.0129 | 100% |

### multiagent vs agent (three_way_smoke dev)

| Type | Questions | agent | multiagent | Difference [95% CI] | P(B better) | Cost: agent | Cost: multiagent |
|---|---|---|---|---|---|---|---|
| lookup | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 | $0.0043 | $0.0125 |
| callers | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 | $0.0046 | $0.0133 |
| **all** | 6 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 | $0.0044 | $0.0129 |
```

**These numbers measure three scripts, not three systems.** The costs are `FakeLLM`'s fixed
token counts at the configured prices, so the stage shares say only that a `large` call costs
more than a few `small` ones. The tables show that the comparison runs for three systems, puts
an interval on every score and every difference, and reports cost per question and per stage.
Whether the pipeline answers better than one agent, and what that costs, is item 17 of the live
pass.

## The web app

```bash
cd frontend
npm run lint && npm run typecheck && npm test
npx playwright test
```

In mock mode, a question asked in Multi-agent mode shows the plan, each branch's steps under its
sub-question, the writing and verification steps, the verified answer with the claim the
verifier left out and its reason, and a trace whose tree has one labeled group per branch
(`e2e/mock/multiagent.spec.ts`, three tests; one checks that nothing overflows the panel or
covers the mode toggle).

## The whole suite

```bash
cd backend && uv run poe check
cd frontend && npm run lint && npm run typecheck && npm test
cd frontend && npx playwright test
python scripts/check_milestones.py
```

- Backend (`poe check`: ruff, mypy on `src/` and `tests/`, every unit and integration test, docs lint): 2,234 passed, 1 skipped, 8 deselected.
- Frontend: lint and typecheck clean; 335 unit tests in 40 files.
- Playwright: 25 of 25 (8 against the real API with the offline stand-in, 17 in mock mode), 3 of them new for multi-agent mode.
- Docs lint: 18 milestones, 165 requirements, dependencies point backward only.
- The OpenAPI spec, the generated TypeScript types, and `eval/report.schema.json` are current (`scripts/gen_api_types.sh --check`, `repoatlas eval schema --check`).
