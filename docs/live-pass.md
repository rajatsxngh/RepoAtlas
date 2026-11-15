# Live pass

Checks that need spending (Anthropic API calls or hosting) and are deferred until the build is
finished and the owner approves it (decisions
[0020](decisions/0020-build-without-spend-live-pass.md),
[0021](decisions/0021-m13-build-gate-injection-live-pass.md),
[0022](decisions/0022-m14-agent-build-gate-live-pass.md),
[0023](decisions/0023-m15-multi-agent-build-gate-live-pass.md), and
[0024](decisions/0024-m16-incremental-builds-cost-build-gate.md)). Each item is **pending** until it is
run and its result is written here and in its milestone's Results. Nothing on this list may be
reported as passed before then.

Everything the items need is already built and checked without spending, so the pass is
configuration plus one run per item.

| # | From | Check | Needs | Status |
|---|---|---|---|---|
| 1 | M12 | Backend on Railway or Fly with managed Postgres; `vector` and `pg_trgm` confirmed | Hosting account and bill | Pending |
| 2 | M12 | Frontend on Vercel behind the access gate | Vercel project | Pending |
| 3 | M12 | A release through `release.yml` runs migrations; a rollback to the previous release works | Items 1 and 2 | Pending |
| 4 | M12 | On the hosted deployment: a repo builds, a question gets a cited answer from the live model, impact analysis returns a report | Items 1–3, API key, a few cents of calls | Pending |
| 5 | M12 | On the self-host stack with an API key: a question gets a cited answer from the live model | API key, a few cents of calls | Pending |
| 6 | M13 | Live injection suite for one-shot answers, three runs per question: zero canary leaks | API key; capped at $2.00 | Pending |
| 7 | M13 | Live injection suite for impact explanations, three runs per question: zero canary leaks | API key; capped at $2.00 | Pending |
| 8 | M13 | Re-record the answer cassette against the hardened prompt (its request was rebased, its response is from 2026-11-11) | API key, about a cent | Pending |
| 9 | M13 | Spans arrive in a real Langfuse project over OTLP/HTTP | Langfuse account (free tier or self-hosted) | Pending |
| 10 | M14 | Re-record the agent cassette from the API (it is hand-written from the documented shapes) | API key, a few cents | Pending |
| 11 | M14 | `agent_max_steps` and the token budgets checked on the dev split | API key; capped at $5.00 | Pending |
| 12 | M14 | Live injection suite in agent mode, three runs per question: zero canary leaks | API key; capped at $3.00 | Pending |
| 13 | M14 | Agent versus one-shot on the test split, three runs each: report with 95% intervals and cost per question | API key; capped at $25.00 (agent) plus $10.00 (one-shot, on the hardened prompt) | Pending |
| 14 | M14 | In the running app, a question in agent mode is answered by the live model with steps in the trace panel | API key, a few cents | Pending |
| 15 | M15 | The multi-agent budgets (steps, tokens, cost, branch time limit) checked on the dev split | API key; capped at $10.00 | Pending |
| 16 | M15 | Live injection suite in multi-agent mode, three runs per question: zero canary leaks | API key; capped at $4.00 | Pending |
| 17 | M15 | Multi-agent versus agent versus one-shot on the test split, three runs each: report with 95% intervals, cost per question, and cost per stage | API key; capped at $40.00 (multi-agent), reusing the reports of item 13 | Pending |
| 18 | M15 | The verifier's labels spot-checked against human labels on dev | Item 15's report; an hour of labeling; no extra calls | Pending |
| 19 | M15 | In the running app, a question in multi-agent mode is answered by the live model, with branches in the trace panel | API key, a few cents | Pending |
| 20 | M16 | Cost and latency per answering mode before any change: one-shot, agent, and multi-agent on dev and on test | The reports of items 11, 13, 15, and 17, plus one-shot on dev; capped at $5.00 for that run | Pending |
| 21 | M16 | Cache reads confirmed: cache-read tokens above zero on a repeated static prefix with tools, and on an agent conversation's second step | API key, a few cents | Pending |
| 22 | M16 | Cheaper routing, step caps, and evidence budgets tried on dev, each kept only inside the tolerance (lower bound of the 95% interval above −0.02 for every question type) | API key; capped at $20.00 | Pending |
| 23 | M16 | The before/after cost report per mode on test, for every setting item 22 kept | API key; capped at the caps of items 13 and 17, and only for modes whose settings changed | Pending |

## How to run them

**Items 1–3 (hosting and release).** Follow "First deployment" in [deploy.md](deploy.md): pick
the provider (the comparison is in decision 0020), create the database and the services, set the
variables and secrets, set `DEPLOY_ENABLED`, and push a version tag. For item 3, release a
second tag, then run the workflow by hand with the first version and check that `/ready` answers
`ahead` and the app still works. The provider commands in the workflow's deploy job follow the
providers' documented CLIs and have not been run; confirm them while provisioning.

**Item 4 (hosted end-to-end check).**

```bash
python scripts/selfhost_check.py --base-url https://<web-host> --token "$TOKEN" --expect-live --json live-check.json
```

It must end with a cited answer whose `answered_by` is `model`. Then repeat the three steps by
hand in the browser. Expected cost: one answer, at most `REPOATLAS_ANSWER_MAX_COST_USD`.

**Item 5 (self-hosted live answer).** In `infra/selfhost.env` set
`REPOATLAS_ANTHROPIC_API_KEY` and `REPOATLAS_LLM_TRANSPORT=anthropic`, run the `up -d` command
of [self-hosting.md](self-hosting.md), and run the same script with `--env-file
infra/selfhost.env --expect-live`. This item needs no hosting and can be run first.

**Items 6 and 7 (live injection suite).** In `backend/`, with `REPOATLAS_ANTHROPIC_API_KEY` set
and Postgres running:

```bash
uv run repoatlas eval run --suite injection --system oneshot --runs 3 --max-cost 2.00
uv run repoatlas eval run --suite injection --system impact_explain --runs 3 --max-cost 2.00
```

The suite builds its own snapshot of `tests/fixtures/repos/injection_lab`. Each command must end
with `complete` and a score of 1.000 (no leak in any run); any leak names the variant's canary in
the report. A leak fails the gate for that mode: fix the prompt or the mode, then run again. Write
the report paths into M13's Results.

**Item 8 (answer cassette).** `uv run poe record-cassettes`, review the diff, and remove the
cassette's `note`.

**Item 9 (Langfuse export).** Set `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS`
as [observability.md](observability.md) describes, ask one question, and check that the trace
appears in the Langfuse project.

**Item 10 (agent cassette).** `uv run poe record-cassettes` also records
`tests/llm_cassettes/agent/` (`tests/live/test_agent_live.py`); review the diff. This is the
first live call with tools, through LiteLLM with the MCP SDK installed next to it, so run it
before anything else in agent mode. A real model picks its own steps: update the expectations
of `tests/integration/agent/test_recorded_agent.py` that name particular steps (which tools,
how many turns, the usage sums) to the recording; its checks of the request rules must pass
unchanged. If the API rejects a request (for example the final call's tool choice `none` next
to a schema, or a thinking block of the small model sent to the large one), fix the loop
before running items 11 to 14.

**Item 11 (agent budgets on dev).** In `backend/`:

```bash
uv run repoatlas eval run --system agent --split dev --runs 1 --max-cost 5.00
```

Read the report's refusals: `budget` refusals and answers that stopped at the step cap say the
defaults of decision 0022 are too tight. Change `REPOATLAS_AGENT_MAX_STEPS`,
`REPOATLAS_AGENT_MAX_INPUT_TOKENS`, or `REPOATLAS_AGENT_PER_TOOL_RESULT_TOKENS`, record the
choice in a decision record, and only then run item 13. Nothing is tuned on the test split.

**Item 12 (live injection suite, agent mode).**

```bash
uv run repoatlas eval run --suite injection --system agent --runs 3 --max-cost 3.00
```

It must end with `complete` and a score of 1.000. A leak in the answer or a tool call outside
the snapshot fails the gate for agent mode; agent mode is not deployed until this passes.

**Item 13 (agent versus one-shot).**

```bash
uv run repoatlas eval run --system oneshot --split test --runs 3 --max-cost 10.00
uv run repoatlas eval run --system agent --split test --runs 3 --max-cost 25.00
uv run repoatlas eval compare <oneshot report>.json <agent report>.json
```

Write the per-type table, the intervals, and the cost per question into M14's Results. If the
agent does not win on the multi-hop question types, say so and explain where it fails, from
the traces.

**Item 14 (agent mode in the app).** With a key set, start the stack (`node scripts/dev.mjs`),
ask a question with the mode set to Agent, and check that steps appear while it runs and that
the answer's citations open the cited code.

**Item 15 (multi-agent budgets on dev).** Run items 10 and 11 first: the explorers are the
agent loop. Then, in `backend/`:

```bash
uv run repoatlas eval run --system multiagent --split dev --runs 1 --max-cost 10.00
```

Read the report's refusals and the cost-per-stage table. `budget` refusals, branches that end
`partial` or `timeout` on ordinary questions, and a verification stage that costs more than
the rest together say the defaults of decision 0023 are wrong. Change
`REPOATLAS_MULTIAGENT_TOTAL_STEPS`, `REPOATLAS_MULTIAGENT_MAX_INPUT_TOKENS`,
`REPOATLAS_MULTIAGENT_MAX_COST_USD`, or `REPOATLAS_MULTIAGENT_BRANCH_TIMEOUT_S`, record the
choice in a decision record, and only then run item 17. This is the first live run of the
planner, the notes call (a response schema next to the tools, with tool choice `none`, on the
small model), the writer, and the verifier: if the API rejects a request, fix the stage first.

**Item 16 (live injection suite, multi-agent mode).**

```bash
uv run repoatlas eval run --suite injection --system multiagent --runs 3 --max-cost 4.00
```

The $4.00 cap is the one planned for this suite, and it was planned for one run per question.
Three runs are 45 answers, so the cap holds only if an answer costs under 9 cents on average;
item 15 tells. A run that reaches the cap stops and is reported incomplete, which is not a
pass: raise the cap only with the owner's OK, and run again (finished answers are cached).

It must end with `complete` and a score of 1.000. A leak in the answer, in a sub-question the
answer shows, or a tool call outside the snapshot fails the gate for multi-agent mode; the
mode is not deployed until this passes.

**Item 17 (three-way report).** With the two reports of item 13:

```bash
uv run repoatlas eval run --system multiagent --split test --runs 3 --max-cost 40.00
uv run repoatlas eval compare <oneshot report>.json <agent report>.json <multiagent report>.json
```

Write the summary table, the paired differences with their intervals, the cost per question,
and the cost per stage into M15's Results. If the pipeline's gain over the agent is small, say
so; the default mode then stays the cheaper one.

**Item 18 (verifier against human labels).** Take 30 claims from item 15's dev report with
`uv run repoatlas eval labels export-claims --report <multiagent dev report>.json`, label them
without looking at the verifier's labels, and compare with the `verification` labels stored in
the cached answers. Report the agreement and every claim the verifier called supported that a
person did not.

**Item 19 (multi-agent mode in the app).** With a key set, start the stack
(`node scripts/dev.mjs`), ask a question that has two independent parts with the mode set to
Multi-agent, and check that the plan and each branch's steps appear while it runs, that the
trace panel groups the spans by branch, and that the answer's citations open the cited code.

**Item 20 (cost baseline).** Items 11, 13, 15, and 17 already produce the dev and test
reports of the agent and the pipeline, and the test report of one-shot answers, with the
settings as built. Add one-shot on dev, then print the baseline:

```bash
uv run repoatlas eval run --system oneshot --split dev --runs 1 --max-cost 5.00
uv run repoatlas eval cost-report --before <oneshot>.json --before <agent>.json --before <multiagent>.json
```

Run it once for the dev reports and once for the test reports, and write the tables into M16's
Results. These reports are the "before" of items 22 and 23, so keep them.

**Item 21 (cache reads).** Run it right after item 10, before any evaluation run:

```bash
uv run pytest -m live tests/live/test_prompt_cache_live.py -q
```

It sends a request with tools twice and a two-step tool conversation, and passes when the
second call of each reports cache-read tokens. If the API rejects a `cache_control` field, set
`REPOATLAS_LLM_CACHE_CONVERSATION=false`, fix `repoatlas.llm.compat`, and run it again before
anything else. The reports of items 11 and 15 then show the saving: `eval cost-report` prints
the share of input tokens read from the cache when a report records it.

**Item 22 (cheaper settings on dev).** One candidate at a time, each as environment variables
in front of the same command, compared with item 20's dev report of that mode:

```bash
REPOATLAS_AGENT_MAX_STEPS=8 uv run repoatlas eval run --system agent --split dev --runs 3 --max-cost 5.00
uv run repoatlas eval cost-report --before <agent dev before>.json --after <agent dev candidate>.json
```

Candidates worth trying, from [performance.md](performance.md): fewer agent steps
(`REPOATLAS_AGENT_MAX_STEPS`), smaller tool results (`REPOATLAS_AGENT_PER_TOOL_RESULT_TOKENS`),
a smaller evidence budget for one-shot answers and the writer
(`REPOATLAS_ANSWER_EVIDENCE_TOKENS`), fewer pipeline steps
(`REPOATLAS_MULTIAGENT_TOTAL_STEPS`), and a lower effort for the `large` role
(`REPOATLAS_LLM_LARGE_EFFORT`). The report's verdict line says `kept` or `rejected` under the
tolerance rule; it needs three runs per question on both sides to mean much. Record every
candidate tried, kept or not, in a decision record. Nothing is tuned on the test split.

**Item 23 (before/after on test).** For each mode with a kept setting, run the test split
with it and compare with the reports of items 13 and 17:

```bash
uv run repoatlas eval cost-report --before <before reports> --after <after reports> --out eval/reports/<date>-cost-report.md
```

Write the tables and the verdicts into M16's Results. If no candidate was kept, say so: the
report then shows prompt caching alone, from item 21's dev comparison.

## Results

_None yet. Add a dated entry per item when it is run: the command, the outcome, the cost._
