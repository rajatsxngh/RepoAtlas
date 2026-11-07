import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { createInlineLayoutClient } from "./layoutClient";
import type { PlannedContainer } from "./layoutPlan";
import { ROOT } from "./model";
import { containerSize, leafSize } from "./sizes";
import { layoutRequest, settle, useMapLayout, type BaseLayout } from "./useMapLayout";

const leaf = (label: string) => ({ id: label, label, nested: false, leaf: leafSize(label) });

function root(children: PlannedContainer["children"], signature = "root"): PlannedContainer {
  return { id: ROOT, label: "", children, edges: [], signature, depth: 0 };
}

describe("settle", () => {
  it("stretches a container when a child grew since it was laid out", () => {
    const base = new Map<string, BaseLayout>([
      [
        ROOT,
        {
          id: ROOT,
          signature: "root",
          positions: { a: { x: 0, y: 0 }, b: { x: 0, y: 100 } },
          sizes: { a: leafSize("a"), b: leafSize("b") },
          content: { width: 140, height: 144 },
        },
      ],
      [
        "a",
        {
          id: "a",
          signature: "a",
          positions: { x: { x: 0, y: 0 }, y: { x: 180, y: 0 } },
          sizes: { x: leafSize("x"), y: leafSize("y") },
          content: { width: 320, height: 44 },
        },
      ],
    ]);
    const structure: PlannedContainer[] = [
      {
        id: "a",
        label: "a",
        children: [leaf("x"), leaf("y")],
        edges: [],
        signature: "a",
        depth: 1,
      },
      root([{ ...leaf("a"), nested: true }, leaf("b")]),
    ];
    const placed = settle(structure, base);
    const aSize = containerSize({ width: 320, height: 44 }, "a");
    expect(placed.get(ROOT)!.sizes.a).toEqual(aSize);
    expect(placed.get(ROOT)!.positions.b!.y).toBe(100 + aSize.height - leafSize("a").height);
  });

  it("asks ELK for nested containers in the same request and sizes the rest", () => {
    const placed = new Map([
      ["c", { positions: {}, sizes: {}, content: { width: 400, height: 90 } }],
    ]);
    const dirty: PlannedContainer[] = [
      { id: "a", label: "a", children: [leaf("x")], edges: [], signature: "a", depth: 1 },
      root([{ ...leaf("a"), nested: true }, { ...leaf("c"), nested: true }, leaf("b")]),
    ];
    const [, top] = layoutRequest(dirty, placed);
    expect(top!.children).toEqual([
      { id: "a", ...leafSize("a"), nested: true },
      { id: "c", ...containerSize({ width: 400, height: 90 }, "c"), nested: false },
      { id: "b", ...leafSize("b"), nested: false },
    ]);
  });
});

describe("useMapLayout", () => {
  it("lays out new containers and keeps closed ones for later", async () => {
    const client = createInlineLayoutClient();
    const collapsed = [root([leaf("a"), leaf("b")])];
    const expanded = [
      {
        id: "a",
        label: "a",
        children: [leaf("x"), leaf("y")],
        edges: [{ source: "x", target: "y" }],
        signature: "a",
        depth: 1,
      },
      root([{ ...leaf("a"), nested: true }, leaf("b")]),
    ];
    const { result, rerender } = renderHook(({ structure }) => useMapLayout(structure, client), {
      initialProps: { structure: collapsed },
    });
    expect(result.current.pending.has(ROOT)).toBe(true);
    await waitFor(() => expect(result.current.pending.size).toBe(0));
    const before = result.current.placed.get(ROOT)!;

    rerender({ structure: expanded });
    expect([...result.current.pending]).toEqual(["a"]);
    await waitFor(() => expect(result.current.pending.size).toBe(0));
    const after = result.current.placed.get(ROOT)!;
    expect(after.positions.a).toEqual(before.positions.a);
    expect(after.sizes.a!.height).toBeGreaterThan(before.sizes.a!.height);
    expect(result.current.lastMs).not.toBeNull();

    act(() => rerender({ structure: collapsed }));
    expect(result.current.pending.size).toBe(0);
    expect(result.current.placed.get(ROOT)!.positions).toEqual(before.positions);
    act(() => rerender({ structure: expanded }));
    expect(result.current.pending.size).toBe(0);
  });
});
