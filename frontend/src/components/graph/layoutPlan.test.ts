import { describe, expect, it } from "vitest";
import type { ApiNode } from "@/lib/api/types";
import { layoutDirection, planLayout } from "./layoutPlan";
import { ROOT, type VisibleEdge, type VisibleNode } from "./model";

function node(key: string, parent: string | null, expanded = false, depth = 0): VisibleNode {
  const apiNode = {
    key,
    kind: key.startsWith("dir:") ? "dir" : "file",
    name: key,
    path: key,
    meta: {},
  } as ApiNode;
  return { type: "node", id: key, node: apiNode, parent, expanded, depth };
}

function edge(source: string, target: string, count = 1): VisibleEdge {
  return {
    id: `${source}|${target}|calls`,
    source,
    target,
    kind: "calls",
    count,
    minConfidence: 1,
    maxConfidence: 1,
  };
}

describe("planLayout", () => {
  const visible = [
    node("dir:a", null, true),
    node("dir:a/x", "dir:a", true, 1),
    node("file:a/x/1.py", "dir:a/x", false, 2),
    node("file:a/2.py", "dir:a", false, 1),
    node("dir:b", null),
  ];

  it("lists containers children first, with their direct children", () => {
    const planned = planLayout(visible, []);
    expect(planned.map((c) => c.id)).toEqual(["dir:a/x", "dir:a", ROOT]);
    expect(planned[1]!.children.map((c) => [c.id, c.nested])).toEqual([
      ["dir:a/x", true],
      ["file:a/2.py", false],
    ]);
  });

  it("lifts edges to the children of the container where their ends meet", () => {
    const planned = planLayout(visible, [
      edge("file:a/x/1.py", "dir:b"),
      edge("file:a/x/1.py", "file:a/2.py"),
      edge("dir:a/x", "file:a/x/1.py"),
    ]);
    const byId = Object.fromEntries(planned.map((c) => [c.id, c.edges]));
    expect(byId[ROOT]).toEqual([{ source: "dir:a", target: "dir:b", weight: 1 }]);
    expect(byId["dir:a"]).toEqual([{ source: "dir:a/x", target: "file:a/2.py", weight: 1 }]);
    expect(byId["dir:a/x"]).toEqual([]);
  });

  it("changes a container's signature only when its children or edges change", () => {
    const first = planLayout(visible, [edge("file:a/2.py", "dir:b")]);
    const second = planLayout(visible, [edge("file:a/2.py", "dir:b")]);
    expect(second.map((c) => c.signature)).toEqual(first.map((c) => c.signature));
    const third = planLayout(visible.slice(0, 4), [edge("file:a/2.py", "dir:a/x")]);
    expect(third.find((c) => c.id === ROOT)!.signature).not.toBe(
      first.find((c) => c.id === ROOT)!.signature,
    );
    // Weights choose the edges that shape the layout, so a new weight lays out again.
    const heavier = planLayout(visible, [edge("file:a/2.py", "dir:b", 7)]);
    expect(heavier.find((c) => c.id === ROOT)!.edges).toEqual([
      { source: "dir:a", target: "dir:b", weight: 7 },
    ]);
    expect(heavier.find((c) => c.id === ROOT)!.signature).not.toBe(
      first.find((c) => c.id === ROOT)!.signature,
    );
  });
});

describe("layoutDirection", () => {
  const base: VisibleEdge = {
    id: "x",
    source: "dir:a",
    target: "dir:b",
    kind: "calls",
    count: 1,
    minConfidence: 1,
    maxConfidence: 1,
  };

  it("keeps uses as they point and flips test links (the test uses the code)", () => {
    for (const kind of ["imports", "calls", "inherits"] as const) {
      expect(layoutDirection({ ...base, kind })).toEqual({ source: "dir:a", target: "dir:b" });
    }
    expect(layoutDirection({ ...base, kind: "tested_by" })).toEqual({
      source: "dir:b",
      target: "dir:a",
    });
  });

  it("leaves route links and pure guesses out of the layout", () => {
    expect(layoutDirection({ ...base, kind: "routes_to" })).toBeNull();
    expect(layoutDirection({ ...base, minConfidence: 0.2, maxConfidence: 0.5 })).toBeNull();
    expect(layoutDirection({ ...base, minConfidence: 0.2, maxConfidence: 0.9 })).not.toBeNull();
  });
});
