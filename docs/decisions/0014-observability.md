# 0014 — Observability: own trace store plus OpenTelemetry

**Status:** Accepted · **Date:** 2026-11-07

## Context

Users should see each answer's steps, tokens, cost, and time, and agent work is hard to debug without traces. Hosted tracing products are convenient, but self-hosting some of them requires several extra services.

## Decision

- RepoAtlas stores its own `traces` and `spans` in Postgres; these power the UI trace panel, cost badges, spend caps, and evaluation cost metrics.
- `trace_span` spans are mirrored as OpenTelemetry spans; FastAPI, SQLAlchemy, and httpx are instrumented.
- Export to Langfuse (or any OTLP/HTTP endpoint) is optional and off unless configured; Langfuse is treated purely as an OTLP destination.

## Consequences

- Traces work in every deployment, including self-hosted ones with no extra services.
- Richer external tooling can be attached without code changes.
