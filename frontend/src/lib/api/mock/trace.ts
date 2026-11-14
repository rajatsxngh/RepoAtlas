/**
 * Mock mode's traces: the shape `GET /traces/{id}` returns, built from what the mock answered.
 * Step names and nesting match the API's (an answer retrieves, calls the model, validates; an
 * impact analysis is one step), with made-up but consistent times.
 */

import type { AnswerView, SpanView, TraceView } from "../types";

const STARTED = Date.parse("2026-01-01T12:00:00.000Z");
const MODEL = "claude-sonnet-5-5";

function spanId(traceId: string, index: number): string {
  return `${traceId.slice(0, 24)}${String(index).padStart(4, "0")}${traceId.slice(28)}`;
}

function at(offsetMs: number): string {
  return new Date(STARTED + offsetMs).toISOString();
}

interface Step {
  name: string;
  kind: string;
  parent: number | null;
  start: number;
  end: number;
  llm?: { tokensIn: number; tokensOut: number; cacheRead: number; cost: number };
  attrs?: Record<string, unknown>;
}

function build(traceId: string, kind: string, snapshotId: number, steps: Step[]): TraceView {
  const spans: SpanView[] = steps.map((step, index) => ({
    id: spanId(traceId, index + 1),
    parent_id: step.parent === null ? null : spanId(traceId, step.parent + 1),
    name: step.name,
    kind: step.kind,
    model: step.llm ? MODEL : null,
    status: "ok",
    started_at: at(step.start),
    ended_at: at(step.end),
    tokens_in: step.llm?.tokensIn ?? null,
    tokens_out: step.llm?.tokensOut ?? null,
    cache_read_tokens: step.llm?.cacheRead ?? null,
    cost_usd: step.llm?.cost ?? null,
    attrs: { duration_ms: step.end - step.start, ...step.attrs },
  }));
  const end = Math.max(...steps.map((step) => step.end));
  return {
    id: traceId,
    kind,
    snapshot_id: snapshotId,
    started_at: at(0),
    ended_at: at(end),
    status: "ok",
    tokens_in: steps.reduce((sum, step) => sum + (step.llm?.tokensIn ?? 0), 0),
    tokens_out: steps.reduce((sum, step) => sum + (step.llm?.tokensOut ?? 0), 0),
    cost_usd: steps.reduce((sum, step) => sum + (step.llm?.cost ?? 0), 0),
    spans,
  };
}

/** The trace of a mock answer or explanation. One refused before the model has no model step. */
export function mockAnswerTrace(
  answer: AnswerView,
  snapshotId: number,
  kind: "answer" | "impact_explain",
): TraceView {
  const called = answer.usage.input_tokens + answer.usage.output_tokens > 0;
  const root = kind === "answer" ? "answer" : "impact.explain";
  const first = kind === "answer" ? "retrieve" : "impact.evidence";
  const steps: Step[] = [
    { name: root, kind: "answer", parent: null, start: 0, end: called ? 1840 : 42 },
    {
      name: first,
      kind: "retrieval",
      parent: 0,
      start: 2,
      end: 40,
      attrs: { evidence: answer.metrics.evidence_count },
    },
  ];
  if (called) {
    steps.push(
      {
        name: "llm.call",
        kind: "llm",
        parent: 0,
        start: 44,
        end: 1810,
        llm: {
          tokensIn: answer.usage.input_tokens + answer.usage.cache_read_tokens,
          tokensOut: answer.usage.output_tokens,
          cacheRead: answer.usage.cache_read_tokens,
          cost: answer.cost_usd,
        },
        attrs: { stop_reason: "end_turn" },
      },
      {
        name: "validate",
        kind: "validation",
        parent: 0,
        start: 1812,
        end: 1838,
        attrs: { citations: answer.metrics.citations_total, valid: answer.metrics.citations_valid },
      },
    );
  }
  return build(answer.trace_id!, kind, snapshotId, steps);
}

/** The trace of a mock impact analysis: one step, no model. */
export function mockImpactTrace(traceId: string, snapshotId: number, items: number): TraceView {
  return build(traceId, "impact", snapshotId, [
    { name: "impact.analyze", kind: "impact", parent: null, start: 0, end: 12, attrs: { items } },
  ]);
}
