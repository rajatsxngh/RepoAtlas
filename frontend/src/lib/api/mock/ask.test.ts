import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { AskPlan, AskStep, AskStepEvent } from "@/lib/api/types";
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

async function askMultiAgent(question: string) {
  const { api } = mockApi();
  const order: string[] = [];
  const steps: AskStepEvent[] = [];
  const plans: AskPlan[] = [];
  const outcome = await api.ask(
    1,
    { question, mode: "multiagent" },
    {
      onStatus: (step) => order.push(`status:${step}`),
      onStep: (event) => {
        steps.push(event);
        order.push(`step:${event.kind}${event.branch ? `@${event.branch}` : ""}`);
      },
      onPlan: (plan) => {
        plans.push(plan);
        order.push("plan");
      },
    },
  );
  return { api, outcome, steps, plans, order };
}

describe("mock multi-agent answers", () => {
  it("stream the plan, each branch's steps, and the stages, in the API's order", async () => {
    const { outcome, steps, plans, order } = await askMultiAgent("How does create_app work?");
    expect(order).toEqual([
      "status:planning",
      "step:plan",
      "plan",
      "status:exploring",
      "step:tool@b1",
      "step:tool@b2",
      "step:tool@b1",
      "step:finish@b2",
      "step:notes@b2",
      "step:finish@b1",
      "step:notes@b1",
      "status:writing",
      "step:write",
      "status:verifying",
      "step:verify",
      "status:validating",
    ]);
    expect(plans).toHaveLength(1);
    expect(plans[0]!.branches.map((branch) => [branch.id, branch.max_steps])).toEqual([
      ["b1", 4],
      ["b2", 3],
    ]);
    expect(plans[0]!.branches[0]!.sub_question).toContain("create_app");
    expect(steps.map((event) => event.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    const { answer } = outcome;
    expect(answer.refused).toBe(false);
    expect(answer.cost_usd).toBeCloseTo(
      steps.reduce((sum, event) => sum + event.cost_usd, 0),
      6,
    );
    const stages = Object.values(answer.stage_costs ?? {});
    expect(stages.reduce((sum, value) => sum + value, 0)).toBeCloseTo(answer.cost_usd, 6);
    expect(Object.keys(answer.stage_costs ?? {})).toEqual([
      "planner",
      "explorers",
      "writer",
      "verifier",
    ]);
    // One drafted claim was left out by the verifier and is not in the answer.
    expect((answer.verification ?? []).map((v) => [v.claim_id, v.label, v.kept])).toEqual([
      ["c1", "supported", true],
      ["c2", "unsupported", false],
    ]);
    expect(answer.claims.map((claim) => claim.id)).toEqual(["c1"]);
    expect(answer.answer_markdown).toContain("Verified statements about the code:");
    expect((answer.branches ?? []).map((branch) => [branch.id, branch.status])).toEqual([
      ["b1", "complete"],
      ["b2", "complete"],
    ]);
    expect((answer.tool_uses ?? []).map((use) => [use.branch, use.name])).toEqual([
      ["b1", "find_definition"],
      ["b2", "find_callers"],
      ["b1", "read_file"],
    ]);
  });

  it("put each branch's spans under its own branch span in the trace", async () => {
    const { api, outcome, steps } = await askMultiAgent("How does create_app work?");
    const trace = await api.getTrace(outcome.answer.trace_id!);
    const spans = new Map(trace.spans.map((span) => [span.id, span]));
    const branchOf = (id: string): string | null => {
      let current = spans.get(id);
      while (current) {
        if (current.name === "multiagent.branch") return String(current.attrs.branch);
        current = current.parent_id ? spans.get(current.parent_id) : undefined;
      }
      return null;
    };
    for (const event of steps) {
      expect(event.span_id).not.toBeNull();
      expect(branchOf(event.span_id!)).toBe(event.branch);
    }
    const root = trace.spans[0]!;
    const top = trace.spans.filter((span) => span.parent_id === root.id).map((span) => span.name);
    expect(top).toEqual([
      "multiagent.context",
      "multiagent.plan",
      "multiagent.branch",
      "multiagent.branch",
      "multiagent.write",
      "validate",
      "multiagent.verify",
      "validate",
    ]);
    expect(trace.cost_usd).toBeCloseTo(outcome.answer.cost_usd, 6);
  });

  it("refuse with one branch when nothing is found", async () => {
    const { outcome, order, plans } = await askMultiAgent("How are refunds sent to the bank?");
    expect(order).toEqual([
      "status:planning",
      "step:plan",
      "plan",
      "status:exploring",
      "step:tool@b1",
      "step:finish@b1",
    ]);
    expect(plans[0]!.branches).toHaveLength(1);
    expect(outcome.answer).toMatchObject({ refused: true, refusal_kind: "evidence" });
    expect(outcome.answer.verification).toEqual([]);
  });
});
