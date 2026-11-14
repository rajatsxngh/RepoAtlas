# Live pass

Checks that need spending (Anthropic API calls or hosting) and are deferred until the build is
finished and the owner approves it (decisions
[0020](decisions/0020-build-without-spend-live-pass.md) and
[0021](decisions/0021-m13-build-gate-injection-live-pass.md)). Each item is **pending** until it is
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
| 8 | M13 | Re-record the answer cassette against the hardened prompt (its request was rebased, its response is from 2026-11-12) | API key, about a cent | Pending |
| 9 | M13 | Spans arrive in a real Langfuse project over OTLP/HTTP | Langfuse account (free tier or self-hosted) | Pending |

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

## Results

_None yet. Add a dated entry per item when it is run: the command, the outcome, the cost._
