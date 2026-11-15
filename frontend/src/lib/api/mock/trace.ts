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

/**
 * The trace of a mock multi-agent answer, built from its `step` events: the plan, one
 * `multiagent.branch` span per branch with that branch's turns, tool calls, and notes under it,
 * then the writing, the verification, and the validations. Returns the events with `span_id`
 * set to their span.
 */
export function mockMultiAgentTrace(
  answer: AnswerView,
  snapshotId: number,
  events: readonly AskStepEvent[],
): { trace: TraceView; steps: AskStepEvent[] } {
  const traceId = answer.trace_id!;
  const steps: Step[] = [
    { name: "answer", kind: "answer", parent: null, start: 0, end: 0 },
    { name: "multiagent.context", kind: "retrieval", parent: 0, start: 1, end: 40 },
  ];
  const linked = new Map<number, string>(); // event seq → span ID
  const llmOf = (event: AskStepEvent) =>
    event.model
      ? {
          tokensIn: event.tokens_in,
          tokensOut: event.tokens_out,
          cacheRead: 0,
          cost: event.cost_usd,
          model: event.model,
        }
      : undefined;
  const stage = (event: AskStepEvent, name: string, start: number, length: number): number => {
    const index = steps.length;
    steps.push({ name, kind: "agent", parent: 0, start, end: start + length });
    const llm = llmOf(event);
    if (llm) {
      steps.push({
        name: "llm.call",
        kind: "llm",
        parent: index,
        start: start + 1,
        end: start + length - 1,
        llm,
      });
    }
    linked.set(event.seq, spanId(traceId, index + 1));
    return start + length + 2;
  };
  let clock = 42;
  for (const event of events) {
    if (event.kind === "plan") clock = stage(event, "multiagent.plan", clock, 900);
  }
  // Branches run side by side: each starts when the plan is done.
  const exploreStart = clock;
  let exploreEnd = clock;
  const branchIds = [...new Set(events.map((event) => event.branch).filter((id) => id !== null))];
  for (const id of branchIds) {
    const summary = answer.branches?.find((branch) => branch.id === id);
    const branchIndex = steps.length;
    steps.push({
      name: "multiagent.branch",
      kind: "agent",
      parent: 0,
      start: exploreStart,
      end: exploreStart,
      attrs: { branch: id, status: summary?.status ?? "complete", steps: summary?.steps ?? 0 },
    });
    let at = exploreStart + 1;
    const turns = new Map<number, number>();
    for (const event of events.filter((candidate) => candidate.branch === id)) {
      const llm = llmOf(event);
      if (event.kind === "notes") {
        const index = steps.length;
        steps.push({
          name: "multiagent.notes",
          kind: "agent",
          parent: branchIndex,
          start: at,
          end: at + 380,
        });
        if (llm) {
          steps.push({
            name: "llm.call",
            kind: "llm",
            parent: index,
            start: at + 1,
            end: at + 379,
            llm,
          });
        }
        at += 382;
        linked.set(event.seq, spanId(traceId, index + 1));
        continue;
      }
      let turn = turns.get(event.step);
      if (turn === undefined) {
        turn = steps.length;
        turns.set(event.step, turn);
        steps.push({
          name: "agent.step",
          kind: "agent",
          parent: branchIndex,
          start: at,
          end: at + 420,
          attrs: { step: event.step },
        });
        if (llm) {
          steps.push({
            name: "llm.call",
            kind: "llm",
            parent: turn,
            start: at + 1,
            end: at + 400,
            llm,
          });
        }
        at += 402;
      }
      if (event.kind === "tool") {
        const index = steps.length;
        steps.push({
          name: `tool.${event.tool}`,
          kind: "tool",
          parent: turn,
          start: at,
          end: at + 8,
          attrs: { arguments: event.arguments, evidence: event.evidence, allowed: event.allowed },
        });
        at += 9;
        steps[turn]!.end = at;
        linked.set(event.seq, spanId(traceId, index + 1));
      } else {
        linked.set(event.seq, spanId(traceId, turn + 1));
      }
    }
    steps[branchIndex]!.end = at;
    exploreEnd = Math.max(exploreEnd, at);
  }
  clock = exploreEnd + 2;
  for (const event of events) {
    if (event.kind === "write") {
      clock = stage(event, "multiagent.write", clock, 1500);
      steps.push({
        name: "validate",
        kind: "validation",
        parent: 0,
        start: clock,
        end: clock + 20,
        attrs: { stage: "draft" },
      });
      clock += 22;
    } else if (event.kind === "verify" || event.kind === "repair") {
      clock = stage(event, `multiagent.${event.kind}`, clock, 800);
    }
  }
  if (events.some((event) => event.kind === "verify")) {
    steps.push({
      name: "validate",
      kind: "validation",
      parent: 0,
      start: clock,
      end: clock + 20,
      attrs: {
        stage: "verified",
        citations: answer.citations.length,
        valid: answer.citations.length,
      },
    });
    clock += 22;
  }
  steps[0]!.end = clock;
  return {
    trace: build(traceId, "answer", snapshotId, steps),
    steps: events.map((event) => ({ ...event, span_id: linked.get(event.seq) ?? null })),
  };
}

/** The trace of a mock impact analysis: one step, no model. */
export function mockImpactTrace(traceId: string, snapshotId: number, items: number): TraceView {
  return build(traceId, "impact", snapshotId, [
    { name: "impact.analyze", kind: "impact", parent: null, start: 0, end: 12, attrs: { items } },
  ]);
}
