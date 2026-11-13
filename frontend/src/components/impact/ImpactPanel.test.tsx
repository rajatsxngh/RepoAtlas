import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "@/lib/api/client";
import type { ImpactResponse } from "@/lib/api/types";
import { renderWithApi } from "@/test/render";
import { ImpactPanel, evidenceLabel, impactLayers, symbolLabel } from "./ImpactPanel";

const ADD = "py:src/shop/cart.py:Cart.add:method";
const GIFT_ADD = "py:src/shop/cart.py:GiftCart.add:method";

const HOSTILE: ImpactResponse = {
  target: { kind: "symbol", symbol_key: "py:a.py:f:function" },
  targets: ["py:a.py:f:function"],
  params: { max_depth: 4, decay: 0.6, min_score: 0.05, limit: 200, edge_kinds: ["calls"] },
  items: [
    {
      symbol_key: "py:a.py:<img src=x onerror=alert(1)>:function",
      kind: "function",
      name: "<img src=x onerror=alert(1)>",
      path: "a.py",
      group: "caller",
      score: 0.5,
      best_score: 0.5,
      depth: 1,
      best_path: [],
      evidence: [],
    },
  ],
  unknown: [
    {
      site: {
        blob_oid: "b".repeat(40),
        path: "a.py",
        start_line: 3,
        end_line: 3,
        symbol_key: null,
      },
      callee_text: "<script>alert(2)</script>",
      candidates: [],
      reason: "string_literal",
    },
  ],
  stats: {
    targets: 1,
    reached: 1,
    expanded: 1,
    items: 1,
    returned: 1,
    truncated: false,
    by_group: { test: 0, public_api: 0, caller: 1 },
    unknown: 1,
    elapsed_ms: 1,
  },
  run_id: 1,
  trace_id: "00000000-0000-4000-8000-000000000001",
};

describe("ImpactPanel", () => {
  it("groups and ranks what a change reaches, and opens an item's evidence", async () => {
    const onOpen = vi.fn();
    const onReport = vi.fn();
    const user = userEvent.setup();
    renderWithApi(
      <ImpactPanel snapshotId={1} symbolKey={ADD} onOpen={onOpen} onReport={onReport} />,
    );
    const panel = screen.getByRole("complementary", { name: "What breaks" });
    expect(panel).toHaveTextContent("What breaks if Cart.add changes?");
    const callers = await within(panel).findByRole("region", { name: "Callers" });
    const tests = within(panel).getByRole("region", { name: "Tests" });
    expect(within(tests).getAllByRole("button").length).toBeGreaterThan(0);
    const add = within(callers).getByTitle(GIFT_ADD);
    expect(add).toHaveTextContent("src/shop/cart.py · direct");
    expect(add).toHaveTextContent("Cart.add called by GiftCart.add");
    await waitFor(() => expect(onReport).toHaveBeenLastCalledWith(expect.objectContaining({})));
    const report = onReport.mock.lastCall![0] as ImpactResponse;
    const scores = report.items.map((item) => item.score);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));

    await user.click(add);
    expect(add).toHaveAttribute("aria-pressed", "true");
    expect(onOpen).toHaveBeenCalledWith(GIFT_ADD);
    const viewer = await within(panel).findByRole("figure");
    expect(viewer).toHaveTextContent("add");
    expect(viewer.querySelectorAll(".ra-code-mark").length).toBeGreaterThan(0);
    expect(within(panel).getByRole("region", { name: "Unknown" })).toHaveTextContent(
      "No unresolved calls or string literals",
    );
  });

  it("renders repo text as text and lists unknown sites as not safe", async () => {
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () => new Response(JSON.stringify(HOSTILE), { status: 200 }),
    });
    renderWithApi(<ImpactPanel snapshotId={1} symbolKey="py:a.py:f:function" />, { api });
    const unknown = await screen.findByRole("region", { name: "Unknown" });
    expect(unknown).toHaveTextContent("<script>alert(2)</script>");
    expect(unknown).toHaveTextContent("a.py:3 · string literal");
    expect(unknown).toHaveTextContent("They are not safe");
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByRole("region", { name: "Callers" })).toHaveTextContent(
      "<img src=x onerror=alert(1)>",
    );
  });

  it("shows errors", async () => {
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () =>
        new Response(
          JSON.stringify({ error: { code: "not_found", message: "No node.", details: {} } }),
          { status: 404 },
        ),
    });
    renderWithApi(<ImpactPanel snapshotId={1} symbolKey="py:a.py:gone:function" />, { api });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("impact helpers", () => {
  it("names keys and ranges", () => {
    expect(symbolLabel(ADD)).toBe("Cart.add");
    expect(symbolLabel("file:src/a.py")).toBe("src/a.py");
    expect(
      evidenceLabel({ blob_oid: "a".repeat(40), path: "src/a.py", start_line: 3, end_line: 5 }),
    ).toBe("a.py:3–5");
  });

  it("builds a scored warning layer and a primary layer for the target", () => {
    const [target, affected] = impactLayers(HOSTILE);
    expect(target).toMatchObject({ style: "primary", nodeKeys: HOSTILE.targets });
    expect(affected).toMatchObject({
      id: "impact",
      style: "warning",
      nodeKeys: [HOSTILE.items[0]!.symbol_key],
      scores: { [HOSTILE.items[0]!.symbol_key]: 0.5 },
    });
  });
});
