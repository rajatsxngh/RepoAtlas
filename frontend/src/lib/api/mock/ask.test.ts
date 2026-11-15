import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { AskStep, AskStepEvent } from "@/lib/api/types";
import { mockApi } from "@/test/render";

async function askAgent(question: string) {
  const { api } = mockApi();
  const statuses: AskStep[] = [];
  const steps: AskStepEvent[] = [];
  const order: string[] = [];
  const outcome = await api.ask(
    1,
    { question, mode: "agent" },
    {
      onStatus: (step) => {
        statuses.push(step);
        order.push(`status:${step}`);
      },
      onStep: (event) => {
        steps.push(event);
        order.push(`step:${event.kind}`);
      },
    },
  );
  return { api, outcome, statuses, steps, order };
}

describe("mock agent answers", () => {
  it("stream a step per tool call, in the order the API sends them", async () => {
    const { outcome, statuses, steps, order } = await askAgent("How does create_app work?");
    expect(statuses).toEqual(["exploring", "answering", "validating"]);
    expect(steps.map((event) => [event.seq, event.step, event.kind, event.tool])).toEqual([
      [1, 1, "tool", "search_code"],
      [2, 2, "tool", "find_definition"],
      [3, 2, "tool", "read_file"],
      [4, 3, "finish", "final_answer"],
      [5, 4, "answer", "answer"],
    ]);
    expect(order).toEqual([
      "status:exploring",
      "step:tool",
      "step:tool",
      "step:tool",
      "step:finish",
      "status:answering",
      "step:answer",
      "status:validating",
    ]);
    const answer = outcome.answer;
    expect(answer.refused).toBe(false);
    expect(answer.tool_uses?.map((use) => use.name)).toEqual([
      "search_code",
      "find_definition",
      "read_file",
    ]);
    // The second tool of a turn reports no tokens: the events add up to the answer.
    expect(steps[2]).toMatchObject({ model: null, tokens_in: 0, cost_usd: 0 });
    const cost = steps.reduce((sum, event) => sum + event.cost_usd, 0);
    expect(answer.cost_usd).toBeCloseTo(cost, 6);
    expect(answer.usage.input_tokens).toBe(steps.reduce((sum, e) => sum + e.tokens_in, 0));
    expect(answer.citations[0]?.evidence_id).toBe("E6");
    expect(outcome.done).toEqual({ run_id: answer.run_id, trace_id: answer.trace_id });
  });

  it("link every step to a span of the answer's trace", async () => {
    const { api, outcome, steps } = await askAgent("How does create_app work?");
    const trace = await api.getTrace(outcome.answer.trace_id!);
    const spans = new Map(trace.spans.map((span) => [span.id, span]));
    expect(steps.map((event) => spans.get(event.span_id!)?.name)).toEqual([
      "tool.search_code",
      "tool.find_definition",
      "tool.read_file",
      "agent.step",
      "agent.answer",
    ]);
    expect(trace.spans.map((span) => span.name)).toEqual([
      "answer",
      "agent.step",
      "llm.call",
      "tool.search_code",
      "agent.step",
      "llm.call",
      "tool.find_definition",
      "tool.read_file",
      "agent.step",
      "llm.call",
      "agent.answer",
      "llm.call",
      "validate",
    ]);
    expect(trace.cost_usd).toBeCloseTo(outcome.answer.cost_usd, 6);
    const models = trace.spans.filter((span) => span.kind === "llm").map((span) => span.model);
    expect(models).toEqual([
      "claude-haiku-5-5",
      "claude-haiku-5-5",
      "claude-haiku-5-5",
      "claude-sonnet-5-5",
    ]);
  });

  it("refuse without a final call when the search finds nothing", async () => {
    const { outcome, statuses, steps } = await askAgent("How are refunds sent to the bank?");
    expect(statuses).toEqual(["exploring"]);
    expect(steps.map((event) => event.kind)).toEqual(["tool", "finish"]);
    expect(steps[0]).toMatchObject({ tool: "search_code", evidence: 0 });
    expect(outcome.answer).toMatchObject({ refused: true, refusal_kind: "evidence" });
    expect(outcome.answer.cost_usd).toBeGreaterThan(0);
  });

  it("leave one-shot answers without steps, and reject unknown modes", async () => {
    const { api } = mockApi();
    const steps: AskStepEvent[] = [];
    const outcome = await api.ask(
      1,
      { question: "How does create_app work?", mode: "oneshot" },
      { onStep: (event) => steps.push(event) },
    );
    expect(steps).toEqual([]);
    expect(outcome.answer.tool_uses ?? []).toEqual([]);
    const bad = api.ask(1, { question: "q", mode: "autonomous" as "agent" });
    await expect(bad).rejects.toBeInstanceOf(ApiError);
    await expect(bad).rejects.toMatchObject({ status: 422, code: "invalid_request" });
  });
});
