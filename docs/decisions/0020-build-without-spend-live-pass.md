# 0020 — Build without spending; live and hosted checks move to a live pass

**Status:** Accepted · **Date:** 2026-11-14

## Context

M07 and M10 made the paid measurements the project needs (about $30 of Anthropic API calls):
the answer smoke run, the recorded cassettes, and the test-split evaluation. M11 cost nothing.

On 2026-11-14 the owner set a constraint for the rest of the build: **no more spending while the
project is being built.** No Anthropic API calls, and no hosting accounts or bills. The owner's
words: "I just want to build the project without more API usage and use Claude Code instead;
once the project is done we can use the API and everything. If the final app requires the API,
we will use it as well once it's done."

The constraint is about *when* money is spent, not about what is built. M12 as planned needs
both kinds of spending: a hosted deployment (Railway or Fly, managed Postgres, Vercel) and an
end-to-end check on it with a live answer.

## Decision

1. **The product stays as designed.** Answers go to Claude through the Anthropic API and
   LiteLLM (decision 0010). Nothing in the product is changed to fit the constraint: no
   subscription-login transport, no feature removed.
2. **Building costs nothing.** From M12 on, no milestone makes a live LLM call, re-records a
   cassette, runs a paid evaluation, or creates a hosting account. Tests use `FakeLLM` and the
   existing cassettes, as before.
3. **An offline stand-in for stack checks.** `REPOATLAS_LLM_TRANSPORT=offline` swaps the
   transport (and only the transport) for one that calls no model: it answers a structured
   request with a draft that cites the first evidence items it was given, costs $0, and says in
   the answer text that no model wrote it. The rest of the path is the real one: retrieval, the
   prompt, the client's request rules and parsing, citation validation, tracing, storage, and
   streaming. It exists so a compose stack or a CI job can check the ask path end to end without
   a key. It proves the plumbing, never answer quality, and it is refused when an API key is
   also set (so a deployment can't run on it by accident).
4. **Checks that need money move to a live pass,** run once after the build is finished and the
   owner approves the spending. Each milestone lists its own live-pass items in its file, marked
   pending, never passed. A milestone closes as Done on its build criteria; its live-pass items
   stay open in [`docs/live-pass.md`](../live-pass.md), which is the single list to run.
5. **Everything the live pass needs is built now,** so the pass is configuration plus one run:
   images, the release workflow with its deploy jobs (switched off), the environment variable
   reference, the gate and the caps, and the runbook.
6. **Later milestones are replanned the same way when their turn comes.** Where a milestone
   needs live calls (a live injection suite, agent and multi-agent evaluation reports, cost
   before and after, a final evaluation), its session writes the no-spend version of the
   affected tasks and exit criteria, adds the live items to the live-pass list, and records the
   change in that milestone's file. Their files are not edited ahead of time.

### What changes in M12

| Planned | At the M12 gate | Live pass |
|---|---|---|
| Backend on Railway or Fly with managed Postgres | Comparison below; nothing provisioned | Owner picks the provider; provision; confirm `vector` and `pg_trgm` |
| Frontend on Vercel | The web app is ready for it: same-origin API proxy, access gate, env reference, a web image for self-hosting | Deploy to Vercel |
| Release workflow builds, pushes, migrates, deploys, smoke-checks | The workflow builds the images and smoke-checks the compose stack in CI; push, migrate, and deploy jobs are written but run only when `DEPLOY_ENABLED` is set | Set secrets and the variable; run a release; roll back once |
| End-to-end check on the deployment with a live answer | The same check (build, ask, impact) on the local self-host stack, the ask through the offline stand-in | The check on the hosted deployment with a real cited answer |

DEP-3 (hosted deployment) is therefore **built for but not delivered** at the M12 gate.

### Provider comparison (on paper)

Compared on what the design notes ask for. Plans and prices change; **confirm each row on the
provider's site before provisioning.** These are the facts as known in November 2026, not
measurements.

| Need | Railway | Fly.io |
|---|---|---|
| Postgres with `vector` and `pg_trgm` | A Postgres service from the `pgvector` image (a template); it is a container with a volume that you operate, with volume backups | Managed Postgres lists `pgvector` among its extensions; an unmanaged Postgres app is the cheaper alternative |
| Worker memory for CPU embeddings (≥ 2 GB) | Per-service limits up to the plan's ceiling, billed by use | Machine sizes chosen per process group; 2 GB shared-CPU machines are available |
| Long-lived connections for server-sent events | Supported; the proxy has an idle limit, which the 15-second keep-alive comments cover | Supported through `fly-proxy`; the same keep-alives apply |
| Scale to zero (pause between sessions) | App sleeping for idle services; the database service keeps running unless stopped | Machines stop and start on demand (`auto_stop_machines`); a worker with no inbound traffic has to be stopped and started by the runbook's commands |
| Migrations before the new release starts | A pre-deploy command per service | `release_command` in `fly.toml` |
| Deploy from CI | CLI with a project token | `flyctl deploy` with a deploy token |

**Leaning:** Railway, because one project holds the API, the worker, and a pgvector Postgres
with the least setup, and the pre-deploy command fits the migration rule. Fly is the better
choice if stopping every machine between sessions matters more than setup time. The owner
decides at the live pass; the images, the workflow, and `docs/deploy.md` fit both.

## Consequences

- The M12 gate shows the stack works when self-hosted: images, migrations, caps, the gate, a
  rollback, and the ask path up to (not including) a live model call. It does not show a hosted
  deployment or a live answer in production; M12's Results says so.
- The minimum-shippable line after M12 holds for self-hosting. A hosted private deployment
  exists only after the live pass.
- Until the live pass, nobody outside the owner's machine can use the product.
- Measured numbers stay honest: everything published so far (M10, M11) was measured live or
  needs no LLM. Anything a later milestone can only measure live is reported as pending, not
  estimated.
- The offline stand-in is one more transport to keep working; a unit test and the compose smoke
  check cover it.
