import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiClient } from "@/lib/api/client";
import type { SpanView, TraceView } from "@/lib/api/types";
import { renderWithApi } from "@/test/render";
import {
  TracePanel,
  TraceToggle,
  formatCost,
  formatDuration,
  formatTokens,
  spanTree,
  traceDurationMs,
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
