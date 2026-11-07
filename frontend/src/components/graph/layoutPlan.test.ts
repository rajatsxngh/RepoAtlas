import { describe, expect, it } from "vitest";
import type { ApiNode } from "@/lib/api/types";
import { planLayout } from "./layoutPlan";
import { ROOT, type VisibleEdge, type VisibleNode } from "./model";

function node(key: string, parent: string | null, expanded = false, depth = 0): VisibleNode {
  const apiNode = {
    key,
    kind: key.startsWith("dir:") ? "dir" : "file",
    name: key,
    path: key,
  } as ApiNode;
  return { type: "node", id: key, node: apiNode, parent, expanded, depth };
}

function edge(source: string, target: string): VisibleEdge {
  return {
    id: `${source}|${target}|calls`,
    source,
    target,
    kind: "calls",
    count: 1,
    minConfidence: 1,
    maxConfidence: 1,
  };
}

const size = () => ({ width: 150, height: 44 });

describe("planLayout", () => {
  const visible = [
    node("dir:a", null, true),
    node("dir:a/x", "dir:a", true, 1),
    node("file:a/x/1.py", "dir:a/x", false, 2),
    node("file:a/2.py", "dir:a", false, 1),
    node("dir:b", null),
  ];

  it("lists containers children first, with their direct children", () => {
    const planned = planLayout(visible, [], size);
    expect(planned.map((c) => c.id)).toEqual(["dir:a/x", "dir:a", ROOT]);
    expect(planned[1]!.children.map((c) => [c.id, c.nested])).toEqual([
      ["dir:a/x", true],
      ["file:a/2.py", false],
    ]);
  });

  it("lifts edges to the children of the container where their ends meet", () => {
    const planned = planLayout(
      visible,
      [
        edge("file:a/x/1.py", "dir:b"),
        edge("file:a/x/1.py", "file:a/2.py"),
        edge("dir:a/x", "file:a/x/1.py"),
      ],
      size,
    );
    const byId = Object.fromEntries(planned.map((c) => [c.id, c.edges]));
    expect(byId[ROOT]).toEqual([{ source: "dir:a", target: "dir:b" }]);
    expect(byId["dir:a"]).toEqual([{ source: "dir:a/x", target: "file:a/2.py" }]);
    expect(byId["dir:a/x"]).toEqual([]);
  });

  it("changes a container's signature only when its children or edges change", () => {
    const first = planLayout(visible, [edge("file:a/2.py", "dir:b")], size);
    const second = planLayout(visible, [edge("file:a/2.py", "dir:b")], size);
    expect(second.map((c) => c.signature)).toEqual(first.map((c) => c.signature));
    const third = planLayout(visible.slice(0, 4), [edge("file:a/2.py", "dir:a/x")], size);
    expect(third.find((c) => c.id === ROOT)!.signature).not.toBe(
      first.find((c) => c.id === ROOT)!.signature,
    );
  });
});
