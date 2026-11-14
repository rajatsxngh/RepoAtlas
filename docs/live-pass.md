# Live pass

Checks that need spending (Anthropic API calls or hosting) and are deferred until the build is
finished and the owner approves it (decisions
[0020](decisions/0020-build-without-spend-live-pass.md),
[0021](decisions/0021-m13-build-gate-injection-live-pass.md), and
[0022](decisions/0022-m14-agent-build-gate-live-pass.md)). Each item is **pending** until it is
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
`tests/llm_cassettes/agent/`; review the diff. If the recorded conversation differs in shape
from the hand-written one (for example in how thinking blocks come back between tool calls),
fix the loop before running items 11 to 14.

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

## Results

_None yet. Add a dated entry per item when it is run: the command, the outcome, the cost._
