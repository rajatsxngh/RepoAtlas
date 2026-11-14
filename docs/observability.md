# Observability

RepoAtlas records what every answer, impact analysis, and impact explanation did: its steps,
the model, tokens, cost, and time. There are two layers (decision
[0014](decisions/0014-observability.md)).

## The trace store (always on)

Each run writes one row to `traces` and one row per step to `spans`, in the same Postgres
database as everything else. The web app's trace panel reads them through
`GET /api/v1/traces/{id}`; the cost badge on an answer, the daily spend cap, and the evaluation
reports' cost numbers come from the same rows. This needs no setup and no extra service.

In code, a step is a `trace_span`:

```python
from repoatlas.tracing import start_trace, trace_span

with start_trace("answer", snapshot_id, sink=sink), trace_span("retrieve", "retrieval") as span:
    ...
    span.set(evidence=12)
```

LLM calls record their own model, tokens, cache tokens, cost, latency, and stop reason.

## OpenTelemetry (optional export)

`repoatlas.observability.otel.configure(settings)` runs when the API or a worker starts. It
instruments:

- **FastAPI** — one server span per API request (`/health` and `/ready` are left out);
- **SQLAlchemy** — one client span per SQL statement, with the statement text and never its
  parameters;
- **httpx** — one client span per outgoing HTTP call (GitHub, the model provider), without
  headers;
- **`trace_span`** — every step is mirrored as a span, nested under the request, with
  `repoatlas.trace_id` (the ID the trace panel uses), `repoatlas.kind`, and for model calls
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
  `gen_ai.usage.cache_read_input_tokens`, and `gen_ai.usage.cost`.

**Nothing leaves the process unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set.** The app behaves the
same with export on or off, and an export that fails (destination down, wrong credentials) is
logged by the exporter and never fails a request.

| Variable | Value |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base URL of the destination. Spans are sent in batches to `<url>/v1/traces` as OTLP/HTTP protobuf. |
| `OTEL_EXPORTER_OTLP_HEADERS` | `key=value` pairs separated by commas; values are URL-decoded. A secret: it is never logged or put on a span. |
| `OTEL_SERVICE_NAME` | Service name on the spans (default `repoatlas`). |

These are the standard OpenTelemetry names, so they have no `REPOATLAS_` prefix (the prefixed
forms work too). Put them in `.env` for development or in `infra/selfhost.env` for the
self-host stack.

### Langfuse

Langfuse accepts OTLP/HTTP, so it needs no Langfuse-specific code. With a project's public and
secret keys:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64 of "public-key:secret-key">
```

Use the host of your Langfuse region or your own instance. Confirm the endpoint path in
Langfuse's OpenTelemetry documentation before relying on it. **This has not been run against a
real Langfuse project yet**: it is item 9 of [`live-pass.md`](live-pass.md). What is tested is
the export itself, against an OTLP/HTTP collector inside the test suite
(`backend/tests/integration/observability/test_export.py`).

### A local collector

Any OTLP/HTTP collector works, for example the OpenTelemetry Collector on its default port:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

## What spans never contain

Prompts, answers, questions, repository text, SQL parameters, request or response headers, API
keys, and the access token. Span attributes are names, counts, and numbers. The security
checklist ([`security-checklist.md`](security-checklist.md)) has the tests.
