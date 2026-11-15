/**
 * Mock mode's traces: the shape `GET /traces/{id}` returns, built from what the mock answered.
 * Step names and nesting match the API's (an answer retrieves, calls the model, validates; an
 * impact analysis is one step), with made-up but consistent times.
 */

import type { AnswerView, AskStepEvent, SpanView, TraceView } from "../types";

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
  llm?: { tokensIn: number; tokensOut: number; cacheRead: number; cost: number; model?: string };
  attrs?: Record<string, unknown>;
}

function build(traceId: string, kind: string, snapshotId: number, steps: Step[]): TraceView {
  const spans: SpanView[] = steps.map((step, index) => ({
    id: spanId(traceId, index + 1),
    parent_id: step.parent === null ? null : spanId(traceId, step.parent + 1),
    name: step.name,
    kind: step.kind,
    model: step.llm ? (step.llm.model ?? MODEL) : null,
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

/**
 * The trace of a mock agent answer, built from its `step` events: a step per model turn with its
 * model call and tool calls under it, then the final answer call and validation. Returns the
 * events with `span_id` set to their span, as the API links them.
 */
export function mockAgentTrace(
  answer: AnswerView,
  snapshotId: number,
  events: readonly AskStepEvent[],
): { trace: TraceView; steps: AskStepEvent[] } {
  const steps: Step[] = [{ name: "answer", kind: "answer", parent: null, start: 0, end: 0 }];
  const linked: AskStepEvent[] = [];
  const turns = new Map<number, number>(); // model turn → index of its agent.step span
  let clock = 2;
  for (const event of events) {
    const llm = event.model
      ? {
          tokensIn: event.tokens_in,
          tokensOut: event.tokens_out,
          cacheRead: 0,
          cost: event.cost_usd,
          model: event.model,
        }
      : undefined;
    if (event.kind === "answer") {
      const parent = steps.length;
      steps.push({
        name: "agent.answer",
        kind: "agent",
        parent: 0,
        start: clock,
        end: clock + 1500,
      });
      if (llm) {
        steps.push({
          name: "llm.call",
          kind: "llm",
          parent,
          start: clock + 2,
          end: clock + 1498,
          llm,
        });
      }
      clock += 1502;
      linked.push({ ...event, span_id: spanId(answer.trace_id!, parent + 1) });
      continue;
    }
    let turn = turns.get(event.step);
    if (turn === undefined) {
      turn = steps.length;
      turns.set(event.step, turn);
      steps.push({
        name: "agent.step",
        kind: "agent",
        parent: 0,
        start: clock,
        end: clock + 420,
        attrs: { step: event.step },
      });
      if (llm) {
        steps.push({
          name: "llm.call",
          kind: "llm",
          parent: turn,
          start: clock + 1,
          end: clock + 400,
          llm,
        });
      }
      clock += 402;
    }
    if (event.kind === "tool") {
      const index = steps.length;
      steps.push({
        name: `tool.${event.tool}`,
        kind: "tool",
        parent: turn,
        start: clock,
        end: clock + 8,
        attrs: { arguments: event.arguments, evidence: event.evidence, allowed: event.allowed },
      });
      clock += 9;
      steps[turn]!.end = clock;
      linked.push({ ...event, span_id: spanId(answer.trace_id!, index + 1) });
    } else {
      linked.push({ ...event, span_id: spanId(answer.trace_id!, turn + 1) });
    }
  }
  if (events.some((event) => event.kind === "answer")) {
    steps.push({
      name: "validate",
      kind: "validation",
      parent: 0,
      start: clock,
      end: clock + 20,
      attrs: { citations: answer.metrics.citations_total, valid: answer.metrics.citations_valid },
    });
    clock += 22;
  }
  steps[0]!.end = clock;
  return { trace: build(answer.trace_id!, "answer", snapshotId, steps), steps: linked };
}

/** The trace of a mock impact analysis: one step, no model. */
export function mockImpactTrace(traceId: string, snapshotId: number, items: number): TraceView {
  return build(traceId, "impact", snapshotId, [
    { name: "impact.analyze", kind: "impact", parent: null, start: 0, end: 12, attrs: { items } },
  ]);
}
