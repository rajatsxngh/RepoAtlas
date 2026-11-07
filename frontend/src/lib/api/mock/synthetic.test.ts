import { describe, expect, it } from "vitest";
import { MockGraph } from "./graph";
import { SYNTHETIC_NODES, syntheticGraph } from "./fixtures/synthetic";

describe("synthetic graph", () => {
  const data = syntheticGraph();
  const keys = new Set(data.nodes.map((node) => node.key));

  it("has exactly the requested number of nodes, all reachable from the root", () => {
    expect(data.nodes).toHaveLength(SYNTHETIC_NODES);
    expect(keys.size).toBe(SYNTHETIC_NODES);
    for (const node of data.nodes) {
      if (node.key === "dir:.") continue;
      expect(keys.has(node.parent_key!), node.key).toBe(true);
    }
    expect(syntheticGraph({ nodes: 500 }).nodes).toHaveLength(500);
  });

  it("is deterministic", () => {
    const again = syntheticGraph();
    expect(again.nodes.map((node) => node.key)).toEqual(data.nodes.map((node) => node.key));
    expect(again.edges.length).toBe(data.edges.length);
  });

  it("is deep and densely connected", () => {
    const dirs = data.nodes.filter((node) => node.kind === "dir");
    const depth = Math.max(
      ...dirs.map((dir) => (dir.path === "." ? 0 : dir.path.split("/").length)),
    );
    expect(depth).toBeGreaterThanOrEqual(6);
    const callable = data.nodes.filter((node) => ["function", "method"].includes(node.kind)).length;
    const calls = data.edges.filter((edge) => edge.kind === "calls").length;
    expect(calls).toBeGreaterThan(callable * 2);
    for (const edge of data.edges) {
      expect(keys.has(edge.src) && keys.has(edge.dst), `${edge.src} -> ${edge.dst}`).toBe(true);
    }
    expect(data.edges.some((edge) => edge.confidence < 0.6)).toBe(true);
  });

  it("serves code whose lines match the symbol spans", () => {
    const graph = new MockGraph(data);
    const fn = data.nodes.find((node) => node.kind === "function")!;
    const slice = graph.code(fn.path, fn.span!.start_line, fn.span!.end_line);
    expect(slice.text.split("\n")[0]).toContain(fn.name);
    const root = graph.subgraph("dir:.", "dir");
    expect(root.nodes.length).toBeGreaterThan(1);
    expect(root.edges.length).toBeGreaterThan(0);
  });
});
