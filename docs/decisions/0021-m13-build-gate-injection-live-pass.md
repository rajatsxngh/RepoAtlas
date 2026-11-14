# 0021 — M13 without spend: a build gate for the injection suite, and what moved

**Status:** Accepted · **Date:** 2026-11-15

## Context

M13 was planned with a live gate: run the injection suite against the real model for one-shot
answers and impact explanations, three times per question, and require zero canary leaks.
Decision 0020 says the build spends nothing, so that run can't happen now. Three more things
came up while planning the milestone:

- **Impact explanations have no surface.** M11 built `explain_impact` but left its endpoint and
  button in the backlog. M13's contract ("the trace panel opens from any answer or impact
  explanation") and its gate treat explanations as an answering mode users reach.
- **Hardening the prompt changes a recorded request.** Flagged spans and the new system-message
  rule change the request that the one live-recorded answer cassette
  (`tests/llm_cassettes/answers/py_small_cart_total.json`) must match. Re-recording it costs
  API calls.
- **The injection suite doesn't fit the runner as it is.** The runner finds snapshots through
  the sandbox recipes of the benchmark repos, runs the claim checker and the judge on every
  answer, and has no registered system for the product's one-shot mode or for explanations.

The owner chose, on 2026-11-15: build the explanation endpoint and button in M13; keep the
recorded response and rewrite only the cassette's request.

## Decision

1. **The product is unchanged by the constraint.** The detector, the hardened prompt, the
   allowlists, the trace panel, OpenTelemetry, and the injection suite are built as designed.
2. **The M13 gate is a build gate.** Everything is tested without a model:
   - the prompt layout, detector, and allowlist tests (`pytest -m injection_static`);
   - the injection suite end to end through the real runner on the built fixture snapshot, with
     two scripted stand-ins in place of the model. An *obedient* one does what every planted
     instruction says, and the scorer must catch each variant by its own canary. A *careful* one
     answers from the evidence and must score zero leaks. This proves the fixture reaches the
     prompt, the scorer sees leaks, and the command works. It says nothing about how the real
     model behaves.
3. **The live gate moves to the live pass.** "Zero canary leaks on the live injection suite for
   one-shot answers and impact explanations" is pending in [`docs/live-pass.md`](../live-pass.md)
   and in M13's file, never passed. The gate rule itself stands: an answering mode ships only
   with zero leaks on the live suite. Until the live pass no mode has met it, and the product is
   not deployed anywhere (decision 0020), so nothing ships before it is checked.
4. **Running it later is configuration plus one command per mode.** With an API key set:
   `repoatlas eval run --suite injection --system oneshot --runs 3 --max-cost 2.00`, and the
   same with `--system impact_explain`. The suite builds its own snapshot of the fixture.
5. **Impact explanations get their surface in M13** (from the backlog):
   `POST /snapshots/{id}/impact/{run_id}/explain` returns the cited explanation of a stored
   impact run as an `AnswerView`, is stored in `qa_runs` with mode `impact_explain`, counts as
   a question for rate limits and the spend cap, and has a button in the impact panel.
6. **The answer cassette is rebased, not re-recorded.** Its request is rewritten to the
   hardened prompt; its response stays the one recorded from the API on 2026-11-11. The
   cassette gains a `note` saying so and keeps `"source": "live"`. Replay still fails on any
   further request change. Re-recording it is a live-pass item. The cassette shows that the
   request shape is the one the client sends and that a real response parses and validates; it
   no longer shows what the model answers to the current prompt.
7. **The OTLP export is checked against a collector inside the test** (an HTTP server that
   accepts OTLP/HTTP and decodes the spans), not against a hosted Langfuse. Sending to a real
   Langfuse project needs an account and is a live-pass item.
8. **Detector false positives are measured on the benchmark snapshots already in the local
   database** (built for M10), by scanning their stored chunks. No model is involved.

### Contract additions in earlier milestones

All are backward compatible; the milestone files list them under "Added later by decision
0021".

- **Eval runner (M10).** `register_suite(…, prepare=None, enrichers=True)`: a suite may bring
  its own snapshots and choose its questions per system (`prepare(context, system, items) ->
  SuitePlan`), and may switch off the claim checker and the judge. `SystemOutput` gains
  `tool_calls` (empty for modes without tools) and `evidence_flags`. Answer systems `oneshot`
  (the product's configuration) and `impact_explain` are registered.
- **Answer contracts (M07).** Evidence items carry `flags`; `Citation` gains `flags` and
  `AnswerMetrics` gains `evidence_flagged`; the evidence block header gains a `flags`
  attribute; `SYSTEM_PROMPT` gains the flagged-span rule. `Cassette` gains an optional `note`.
  `trace_span` spans are mirrored as OpenTelemetry spans.
- **Impact (M11).** The explanation endpoint above; `ImpactExplainer` built from settings by
  `repoatlas.impact.explain.explainer_from_settings`.
- **Store (M03).** `GraphStore.read_code` goes through the allowlist helpers (same errors as
  before for a path outside the snapshot).

### What the earlier numbers mean now

The M10 and M11 reports were measured with the prompt as it was before hardening. The system
prompt's hash is part of every answer system's configuration fingerprint, so cached answers
from those runs are not reused for the hardened prompt, and any later evaluation measures the
hardened prompt.

## Consequences

- M13 closes as Done on its build criteria with four live-pass items open (two suite runs, the
  cassette, the Langfuse export).
- The build gate can't detect a model that follows a planted instruction; only the live run
  can. The docs say so wherever the gate is described.
- The scripted stand-ins are test code; they are not selectable in a deployment.
