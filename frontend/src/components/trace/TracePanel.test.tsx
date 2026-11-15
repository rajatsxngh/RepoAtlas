import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiClient } from "@/lib/api/client";
import type { AskStepEvent, SpanView, TraceView } from "@/lib/api/types";
import { renderWithApi } from "@/test/render";
import {
  formatCost,
  formatDuration,
  formatTokens,
  groupSteps,
  LiveSteps,
  spanTree,
  traceDurationMs,
  TracePanel,
  TraceToggle,
} from "./TracePanel";

const TRACE_ID = "11111111-1111-4111-8111-111111111111";

function span(id: number, parent: number | null, extra: Partial<SpanView> = {}): SpanView {
  const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  return {
    id: uuid(id),
    parent_id: parent === null ? null : uuid(parent),
    name: `step-${id}`,
    kind: "step",
    model: null,
    status: "ok",
    started_at: "2026-01-01T00:00:00.000Z",
    ended_at: "2026-01-01T00:00:00.100Z",
    tokens_in: null,
    tokens_out: null,
    cache_read_tokens: null,
    cost_usd: null,
    attrs: {},
    ...extra,
  };
}

const TRACE: TraceView = {
  id: TRACE_ID,
  kind: "answer",
  snapshot_id: 1,
  started_at: "2026-01-01T00:00:00.000Z",
  ended_at: "2026-01-01T00:00:02.000Z",
  status: "ok",
  tokens_in: 12_400,
  tokens_out: 310,
  cost_usd: 0.0271,
  spans: [
    span(1, null, { name: "answer", kind: "answer", ended_at: "2026-01-01T00:00:02.000Z" }),
    span(2, 1, { name: "retrieve", kind: "retrieval", attrs: { duration_ms: 38.5 } }),
    span(3, 1, {
      name: "llm.call",
      kind: "llm",
      model: "claude-sonnet-5-5",
      started_at: "2026-01-01T00:00:00.100Z",
      ended_at: "2026-01-01T00:00:01.900Z",
      tokens_in: 12_400,
      tokens_out: 310,
      cache_read_tokens: 1_000,
      cost_usd: 0.0271,
    }),
    span(4, 1, {
      name: "<img src=x onerror=alert(1)>",
      kind: "validation",
      status: "error",
      started_at: "2026-01-01T00:00:01.900Z",
      ended_at: "2026-01-01T00:00:02.000Z",
      attrs: { error: "ValueError: <script>alert(2)</script>" },
    }),
  ],
};

function apiFor(trace: TraceView | null): ApiClient {
  return new ApiClient({
    baseUrl: "http://x/api/v1",
    fetch: async () =>
      trace
        ? new Response(JSON.stringify(trace), { status: 200 })
        : new Response(
            JSON.stringify({
              error: { code: "not_found", message: "No trace.", details: { resource: "trace" } },
            }),
            { status: 404 },
          ),
  });
}

describe("trace helpers", () => {
  it("builds the span tree in start order, with offsets and durations", () => {
    const [root, ...rest] = spanTree(TRACE);
    expect(rest).toEqual([]);
    expect(root!.span.name).toBe("answer");
    expect(root!.children.map((child) => child.span.name)).toEqual([
      "retrieve",
      "llm.call",
      "<img src=x onerror=alert(1)>",
    ]);
    expect(root!.children.map((child) => child.depth)).toEqual([1, 1, 1]);
    expect(root!.children[0]!.durationMs).toBe(38.5); // the recorded duration wins
    expect(root!.children[1]).toMatchObject({ offsetMs: 100, durationMs: 1800 });
    expect(traceDurationMs(TRACE)).toBe(2000);
  });

  it("keeps spans whose parent is missing, and survives a parent cycle", () => {
    const orphan = { ...TRACE, spans: [span(5, 99), span(6, 5)] };
    expect(spanTree(orphan).map((node) => node.span.name)).toEqual(["step-5"]);
    const cycle = { ...TRACE, spans: [span(7, 8), span(8, 7), span(9, null)] };
    expect(spanTree(cycle).map((node) => node.span.name)).toEqual(["step-9"]);
    expect(spanTree({ ...TRACE, spans: [] })).toEqual([]);
    expect(traceDurationMs({ ...TRACE, ended_at: null, spans: [] })).toBe(0);
  });

  it("formats times, tokens, and cost", () => {
    expect(formatDuration(0.4)).toBe("<1 ms");
    expect(formatDuration(38.5)).toBe("39 ms");
    expect(formatDuration(1840)).toBe("1.84 s");
    expect(formatDuration(12_345)).toBe("12.3 s");
    expect(formatTokens(310)).toBe("310");
    expect(formatTokens(2_400)).toBe("2.4k");
    expect(formatTokens(12_400)).toBe("12k");
    expect(formatCost(0.0271)).toBe("$0.0271");
    expect(formatCost(0)).toBe("$0.0000");
  });
});

describe("TracePanel", () => {
  it("shows totals and every step with its kind, model, tokens, cost, and time", async () => {
    renderWithApi(<TracePanel traceId={TRACE_ID} />, { api: apiFor(TRACE) });
    const panel = screen.getByRole("region", { name: "Trace" });
    const totals = await within(panel).findByTestId("trace-totals");
    expect(totals).toHaveTextContent("Time2.00 s");
    expect(totals).toHaveTextContent("Tokens in12k");
    expect(totals).toHaveTextContent("Tokens out310");
    expect(totals).toHaveTextContent("Cost$0.0271");
    expect(panel).toHaveTextContent("answer · 4 steps");
    const tree = within(panel).getByRole("tree", { name: "Steps" });
    const items = within(tree).getAllByRole("treeitem");
    expect(items).toHaveLength(4);
    const llm = items.find((item) => item.getAttribute("data-span-kind") === "llm")!;
    expect(llm).toHaveTextContent("llm.call");
    expect(llm).toHaveTextContent("claude-sonnet-5-5 · 12k in · 310 out · $0.0271");
    expect(llm).toHaveTextContent("1.80 s");
    expect(llm).toHaveAttribute("aria-level", "2");
    // The bar starts where the step started and is as wide as it took.
    const bar = within(llm).getByTestId("span-bar");
    expect(bar.style.marginLeft).toBe("5%");
    expect(bar.style.width).toBe("90%");
  });

  it("renders span names and errors as text", async () => {
    renderWithApi(<TracePanel traceId={TRACE_ID} />, { api: apiFor(TRACE) });
    const tree = await screen.findByRole("tree", { name: "Steps" });
    expect(tree).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(tree).toHaveTextContent("ValueError: <script>alert(2)</script>");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });

  it("collapses and expands a step's children", async () => {
    const user = userEvent.setup();
    renderWithApi(<TracePanel traceId={TRACE_ID} />, { api: apiFor(TRACE) });
    const tree = await screen.findByRole("tree", { name: "Steps" });
    await user.click(within(tree).getByRole("button", { name: "Collapse answer" }));
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(1);
    expect(within(tree).getByRole("treeitem")).toHaveAttribute("aria-expanded", "false");
    await user.click(within(tree).getByRole("button", { name: "Expand answer" }));
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(4);
  });

  it("says when the trace can't be loaded", async () => {
    renderWithApi(<TracePanel traceId={TRACE_ID} />, { api: apiFor(null) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

function stepEvent(seq: number, fields: Partial<AskStepEvent> = {}): AskStepEvent {
  return {
    seq,
    step: seq,
    kind: "tool",
    branch: null,
    tool: "search_code",
    arguments: 'query="cart total"',
    result_tokens: 1240,
    evidence: 3,
    error: null,
    allowed: true,
    cached: false,
    truncated: false,
    flags: [],
    model: "claude-haiku-5-5",
    tokens_in: 3100,
    tokens_out: 70,
    cost_usd: 0.0003,
    span_id: null,
    ...fields,
  };
}

describe("LiveSteps", () => {
  it("lists each step with what it returned and what its model turn cost", () => {
    renderWithApi(
      <LiveSteps
        steps={[
          stepEvent(1),
          stepEvent(2, {
            tool: "read_file",
            arguments: 'path="src/cart.py", start=1, end=40',
            evidence: 1,
            model: null,
            tokens_in: 0,
            tokens_out: 0,
            cost_usd: 0,
            truncated: true,
          }),
          stepEvent(3, {
            kind: "finish",
            tool: "final_answer",
            arguments: "",
            evidence: 0,
            result_tokens: 0,
          }),
          stepEvent(4, {
            kind: "answer",
            tool: "answer",
            arguments: "",
            model: "claude-sonnet-5-5",
            tokens_in: 5200,
            tokens_out: 310,
            cost_usd: 0.0135,
          }),
        ]}
        running
      />,
    );
    const region = screen.getByRole("region", { name: "Agent steps" });
    expect(region).toHaveAttribute("aria-busy", "true");
    const rows = within(region).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("search_code");
    expect(rows[0]).toHaveTextContent("3 pieces of evidence · about 1.2k tokens returned");
    expect(rows[0]).toHaveTextContent("claude-haiku-5-5 · 3.1k in · 70 out · $0.0003");
    expect(rows[1]).toHaveTextContent("1 piece of evidence");
    expect(rows[1]).toHaveTextContent("cut to fit");
    expect(rows[1]).not.toHaveTextContent("claude-haiku-5-5"); // reported once per model turn
    expect(rows[2]).toHaveTextContent("Finished exploring");
    expect(rows[3]).toHaveTextContent("Wrote the answer");
    expect(rows[3]).toHaveTextContent("claude-sonnet-5-5 · 5.2k in · 310 out · $0.0135");
    expect(within(region).getByTestId("steps-summary")).toHaveTextContent("2 tool calls · $0.0141");
  });

  it("marks refused, failed, repeated, and flagged calls", () => {
    renderWithApi(
      <LiveSteps
        steps={[
          stepEvent(1, {
            tool: "read_file",
            arguments: 'path="/etc/passwd"',
            allowed: false,
            error: "'/etc/passwd' is not a file of this snapshot: the path is absolute.",
            evidence: 0,
          }),
          stepEvent(2, {
            tool: "find_callers",
            error: "The snapshot has no symbol with the key 'py:x'.",
            evidence: 0,
          }),
          stepEvent(3, { cached: true, evidence: 0, result_tokens: 40 }),
          stepEvent(4, { flags: ["instruction_to_assistant"] }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveAttribute("data-refused", "true");
    expect(rows[0]).toHaveTextContent("Refused: '/etc/passwd' is not a file of this snapshot");
    expect(rows[0]).not.toHaveTextContent("pieces of evidence");
    expect(rows[1]).not.toHaveAttribute("data-refused");
    expect(rows[1]).toHaveTextContent("Failed: The snapshot has no symbol");
    expect(rows[2]).toHaveTextContent("same call as before, not run again");
    expect(rows[3]).toHaveTextContent("contains instruction-like text, marked as data");
    expect(screen.getByTestId("steps-summary")).toHaveTextContent("4 tool calls");
    expect(screen.getByRole("region", { name: "Agent steps" })).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });

  it("renders tool names, arguments, and errors as text, never as HTML", () => {
    const hostile = "<img src=x onerror=alert(1)><script>alert(2)</script>";
    renderWithApi(
      <LiveSteps steps={[stepEvent(1, { tool: hostile, arguments: hostile, error: hostile })]} />,
    );
    const region = screen.getByRole("region", { name: "Agent steps" });
    expect(region.querySelector("img, script")).toBeNull();
    expect(region).toHaveTextContent("<script>alert(2)</script>");
  });
});

describe("TraceToggle", () => {
  it("is a cost badge that opens the trace", async () => {
    const user = userEvent.setup();
    renderWithApi(<TraceToggle traceId={TRACE_ID} costUsd={0.0062} tokens={2710} />, {
      api: apiFor(TRACE),
    });
    const badge = screen.getByTestId("cost-badge");
    expect(badge).toHaveTextContent("$0.0062 · 2.7k tokens · Trace");
    expect(badge).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Trace" })).toBeNull();
    await user.click(badge);
    expect(badge).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("tree", { name: "Steps" })).toBeInTheDocument();
    await user.click(badge);
    expect(screen.queryByRole("region", { name: "Trace" })).toBeNull();
  });

  it("shows the cost alone when the run has no trace", () => {
    renderWithApi(<TraceToggle traceId={null} costUsd={0.01} />);
    const badge = screen.getByTestId("cost-badge");
    expect(badge.tagName).toBe("SPAN");
    expect(badge).toHaveTextContent("$0.0100");
  });
});

describe("LiveSteps of a multi-agent answer", () => {
  const PLAN = {
    branches: [
      { id: "b1", sub_question: "How is the total computed?", seed_symbols: [], max_steps: 4 },
      { id: "b2", sub_question: "<b>What rounds money?</b>", seed_symbols: [], max_steps: 3 },
    ],
  };
  // As a server sends them: the plan, then the two branches' steps mixed, then the stages.
  const EVENTS: AskStepEvent[] = [
    stepEvent(1, {
      kind: "plan",
      tool: "plan",
      arguments: "2 branches",
      model: "claude-sonnet-5-5",
    }),
    stepEvent(2, { branch: "b1", tool: "find_definition", arguments: 'name="total"' }),
    stepEvent(3, { branch: "b2", tool: "search_code", cached: true }),
    stepEvent(4, { branch: "b1", kind: "finish", tool: "final_answer" }),
    stepEvent(5, { branch: "b2", kind: "finish", tool: "final_answer" }),
    stepEvent(6, { branch: "b1", kind: "notes", tool: "notes" }),
    stepEvent(7, { kind: "write", tool: "write", model: "claude-sonnet-5-5", cost_usd: 0.01 }),
    stepEvent(8, { kind: "verify", tool: "verify", model: "claude-sonnet-5-5", cost_usd: 0.005 }),
    stepEvent(9, { kind: "repair", tool: "repair", model: "claude-sonnet-5-5", cost_usd: 0.005 }),
  ];

  it("groups mixed events by branch, in the plan's order, between the stages", () => {
    const groups = groupSteps(EVENTS, PLAN);
    expect(groups.map((group) => [group.branch, group.steps.map((event) => event.seq)])).toEqual([
      [null, [1]],
      ["b1", [2, 4, 6]],
      ["b2", [3, 5]],
      [null, [7, 8, 9]],
    ]);
    // Without a plan, branches appear in the order their first event arrived.
    expect(groupSteps(EVENTS.slice(2, 4)).map((group) => group.branch)).toEqual(["b2", "b1"]);
    // A planned branch that hasn't started yet still has its (empty) group.
    expect(groupSteps(EVENTS.slice(0, 2), PLAN).map((group) => group.steps.length)).toEqual([
      1, 1, 0,
    ]);
    // An agent answer has no branches: one flat group.
    const flat = [stepEvent(1), stepEvent(2, { kind: "answer", tool: "answer" })];
    expect(groupSteps(flat)).toEqual([{ branch: null, steps: flat }]);
  });

  it("shows each branch under its sub-question with its own steps", () => {
    render(<LiveSteps steps={EVENTS} plan={PLAN} running />);
    const region = screen.getByRole("region", { name: "Agent steps" });
    expect(within(region).getByTestId("steps-summary")).toHaveTextContent(
      "2 branches · 2 tool calls",
    );
    const first = within(region).getByRole("region", { name: "Branch b1" });
    expect(first).toHaveTextContent("How is the total computed?");
    expect(within(first).getAllByRole("listitem")).toHaveLength(3);
    expect(within(first).getByText("find_definition")).toBeInTheDocument();
    expect(within(first).getByText("Reported its findings")).toBeInTheDocument();
    expect(within(first).getByTestId("branch-state")).toHaveTextContent("explored");
    const second = within(region).getByRole("region", { name: "Branch b2" });
    expect(within(second).getAllByRole("listitem")).toHaveLength(2);
    expect(within(second).getByTestId("branch-state")).toHaveTextContent("exploring…");
    expect(second).toHaveTextContent("already run for this answer, not run again");
    // The sub-question is model text: shown as text.
    expect(second).toHaveTextContent("<b>What rounds money?</b>");
    expect(second.querySelector("b")).toBeNull();
    // The stages around the branches.
    const before = within(region).getByRole("list", { name: "Before the branches" });
    expect(before).toHaveTextContent("Planned the exploration");
    expect(before).toHaveTextContent("2 branches");
    const after = within(region).getByRole("list", { name: "After the branches" });
    expect(
      within(after)
        .getAllByRole("listitem")
        .map((row) => row.dataset.stepKind),
    ).toEqual(["write", "verify", "repair"]);
    expect(after).toHaveTextContent("Checked the claims against the code");
    expect(after).toHaveTextContent("Rewrote partly supported claims");
  });

  it("says how each branch ended once the answer is there", () => {
    const summary = (id: string, status: "complete" | "partial" | "timeout" | "failed") => ({
      id,
      sub_question: `Part ${id}?`,
      seed_symbols: [],
      status,
      stop: null,
      error: null,
      max_steps: 4,
      steps: 2,
      evidence: 1,
      findings: 1,
      cost_usd: 0.001,
    });
    render(
      <LiveSteps
        steps={EVENTS}
        plan={PLAN}
        branches={[summary("b1", "complete"), summary("b2", "timeout")]}
      />,
    );
    const first = screen.getByRole("region", { name: "Branch b1" });
    const second = screen.getByRole("region", { name: "Branch b2" });
    expect(within(first).getByTestId("branch-state")).toHaveTextContent("explored");
    expect(within(second).getByTestId("branch-state")).toHaveTextContent("ran out of time");
    expect(second).toHaveAttribute("data-branch-status", "timeout");
  });

  it("waits with the plan alone before any branch has taken a step", () => {
    render(<LiveSteps steps={[EVENTS[0]!]} plan={PLAN} running />);
    expect(screen.getAllByTestId("branch-state").map((node) => node.textContent)).toEqual([
      "waiting…",
      "waiting…",
    ]);
  });
});

describe("TracePanel with branches", () => {
  it("labels each branch's span and keeps the branch's steps under it", async () => {
    const trace: TraceView = {
      ...TRACE,
      spans: [
        span(1, null, { name: "answer", kind: "answer" }),
        span(2, 1, { name: "multiagent.plan", kind: "agent" }),
        span(3, 1, {
          name: "multiagent.branch",
          kind: "agent",
          attrs: { branch: "b1", status: "complete" },
        }),
        span(4, 3, { name: "agent.step", kind: "agent" }),
        span(5, 4, { name: "tool.read_file", kind: "tool" }),
        span(6, 1, {
          name: "multiagent.branch",
          kind: "agent",
          attrs: { branch: "b2", status: "timeout" },
        }),
        span(7, 6, { name: "agent.step", kind: "agent" }),
      ],
    };
    const user = userEvent.setup();
    renderWithApi(<TracePanel traceId={TRACE_ID} />, { api: apiFor(trace) });
    const tree = await screen.findByRole("tree", { name: "Steps" });
    const branches = within(tree)
      .getAllByRole("treeitem")
      .filter((item) => item.dataset.branch !== undefined);
    expect(branches.map((item) => item.dataset.branch)).toEqual(["b1", "b2"]);
    expect(within(branches[0]!).getByText("branch b1")).toBeInTheDocument();
    expect(within(branches[0]!).getByTestId("branch-status")).toHaveTextContent("complete");
    expect(within(branches[1]!).getByTestId("branch-status")).toHaveTextContent("timeout");
    // The tool call sits inside its branch, two levels down.
    expect(within(branches[0]!).getByText("tool.read_file")).toBeInTheDocument();
    expect(within(branches[1]!).queryByText("tool.read_file")).toBeNull();
    // A branch folds away as one group.
    await user.click(within(tree).getByRole("button", { name: "Collapse branch b1" }));
    expect(within(tree).queryByText("tool.read_file")).toBeNull();
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(5);
  });
});
