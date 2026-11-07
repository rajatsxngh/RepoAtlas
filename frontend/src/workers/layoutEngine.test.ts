import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it, vi } from "vitest";
import { containerSize } from "@/components/graph/sizes";
import type { ELK as ElkApi, ElkNode } from "elkjs/lib/elk-api";
import {
  COMPACT_PLACEMENT_MAX_CHILDREN,
  SKELETON_EDGES,
  layoutContainers,
  skeletonEdges,
} from "./layoutEngine";

const elk = new ELK();

function overlaps(a: { x: number; y: number; w: number; h: number }, b: typeof a): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

describe("layoutContainers", () => {
  it("places every child without overlaps, top to bottom along edges", async () => {
    const children = Array.from({ length: 12 }, (_, i) => ({
      id: `n${i}`,
      width: 150,
      height: 44,
    }));
    const edges = [
      { source: "n0", target: "n1" },
      { source: "n1", target: "n2" },
      { source: "n0", target: "n3" },
      { source: "n3", target: "n3" },
      { source: "n0", target: "n1" },
      { source: "n0", target: "ghost" },
    ];
    const [result] = await layoutContainers(elk, [{ id: "c", label: "c", children, edges }]);
    expect(Object.keys(result!.positions).sort()).toEqual(children.map((c) => c.id).sort());
    const boxes = children.map((c) => ({ ...result!.positions[c.id]!, w: c.width, h: c.height }));
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1)
        expect(overlaps(boxes[i]!, boxes[j]!)).toBe(false);
    }
    expect(result!.positions.n1!.y).toBeGreaterThan(result!.positions.n0!.y);
    expect(result!.positions.n2!.y).toBeGreaterThan(result!.positions.n1!.y);
    for (const b of boxes) {
      expect(b.x + b.w).toBeLessThanOrEqual(result!.content.width);
      expect(b.y + b.h).toBeLessThanOrEqual(result!.content.height);
    }
    // Isolated nodes wrap into rows instead of one long line.
    expect(result!.content.width / result!.content.height).toBeLessThan(6);
  });

  it("uses nested containers' computed sizes for their parents", async () => {
    const [inner, outer] = await layoutContainers(elk, [
      {
        id: "inner",
        label: "inner",
        children: [
          { id: "a", width: 150, height: 44 },
          { id: "b", width: 150, height: 44 },
        ],
        edges: [{ source: "a", target: "b" }],
      },
      {
        id: "outer",
        label: "outer",
        children: [
          { id: "inner", width: 150, height: 44, nested: true },
          { id: "c", width: 150, height: 44 },
        ],
        edges: [],
      },
    ]);
    expect(outer!.sizes.inner).toEqual(containerSize(inner!.content, "inner"));
    expect(outer!.sizes.c).toEqual({ width: 150, height: 44 });
  });

  it("handles empty containers", async () => {
    const [result] = await layoutContainers(elk, [
      { id: "e", label: "e", children: [], edges: [] },
    ]);
    expect(result).toMatchObject({ positions: {}, content: { width: 0, height: 0 } });
  });
});

describe("skeletonEdges", () => {
  const ids = new Set(["hub", "a", "b", "c", "d", "e"]);

  it("merges duplicates, drops self-loops and unknown ends, and keeps the original order", () => {
    expect(
      skeletonEdges(
        [
          { source: "a", target: "b" },
          { source: "a", target: "a" },
          { source: "a", target: "ghost" },
          { source: "b", target: "c", weight: 2 },
          { source: "a", target: "b", weight: 4 },
        ],
        ids,
      ),
    ).toEqual([
      { source: "a", target: "b", weight: 5 },
      { source: "b", target: "c", weight: 2 },
    ]);
  });

  it("keeps each node's strongest outgoing and incoming edges, ties broken by id", () => {
    const edges = ["a", "b", "c", "d", "e"].map((target, index) => ({
      source: "hub",
      target,
      weight: target === "e" ? 9 : index < 2 ? 1 : 3,
    }));
    // From the hub: e (9), then c and d (3). Every target also keeps its only incoming edge, so
    // with two per node the hub keeps e and c, and a, b, d stay as their targets' strongest.
    expect(skeletonEdges(edges, ids, 2).map((e) => e.target)).toEqual(["a", "b", "c", "d", "e"]);
    // Many sources into one node: only the strongest few shape its place.
    const into = ["a", "b", "c", "d", "e"].map((source, index) => ({
      source,
      target: "hub",
      weight: index + 1,
    }));
    const kept = skeletonEdges(into, ids, 2);
    expect(kept.map((e) => e.source)).toEqual(["a", "b", "c", "d", "e"]);
    const dense = ["a", "b", "c", "d", "e"].flatMap((source) =>
      ["a", "b", "c", "d", "e"]
        .filter((target) => target !== source)
        .map((target) => ({ source, target })),
    );
    const skeleton = skeletonEdges(dense, ids, 1);
    // One strongest edge out of and into each node, all ties: the first other end by id wins.
    // Out: a→b, and b, c, d, e → a. In: b→a, and a → b, c, d, e. The union, in input order:
    expect(skeleton.map((e) => `${e.source}>${e.target}`)).toEqual([
      "a>b",
      "a>c",
      "a>d",
      "a>e",
      "b>a",
      "c>a",
      "d>a",
      "e>a",
    ]);
    expect(skeleton.length).toBeLessThan(dense.length);
    expect(SKELETON_EDGES).toBeGreaterThan(0);
  });
});

describe("layoutContainers placement", () => {
  function recordingElk() {
    const calls: ElkNode[] = [];
    const elk = {
      layout: vi.fn(async (graph: ElkNode) => {
        calls.push(graph);
        return { ...graph, children: (graph.children ?? []).map((c) => ({ ...c, x: 0, y: 0 })) };
      }),
    } as unknown as ElkApi;
    return { elk, calls };
  }

  function container(size: number) {
    return {
      id: `c${size}`,
      label: "c",
      children: Array.from({ length: size }, (_, i) => ({ id: `n${i}`, width: 100, height: 40 })),
      edges: Array.from({ length: size - 1 }, (_, i) => ({ source: "n0", target: `n${i + 1}` })),
    };
  }

  it("places small containers compactly and large ones quickly, on their strongest edges", async () => {
    const { elk, calls } = recordingElk();
    await layoutContainers(elk, [
      container(12),
      container(COMPACT_PLACEMENT_MAX_CHILDREN),
      container(COMPACT_PLACEMENT_MAX_CHILDREN + 1),
    ]);
    const strategy = (graph: ElkNode) =>
      graph.layoutOptions?.["elk.layered.nodePlacement.strategy"];
    expect(calls.map(strategy)).toEqual(["NETWORK_SIMPLEX", "NETWORK_SIMPLEX", "BRANDES_KOEPF"]);
    // The hub's 11 outgoing edges all stay: each is its target's only incoming edge.
    expect(calls[0]!.edges).toHaveLength(11);
  });
});
