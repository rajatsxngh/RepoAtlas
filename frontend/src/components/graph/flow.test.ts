import { describe, expect, it } from "vitest";
import type { ApiNode, EdgeKind } from "@/lib/api/types";
import { createNumbering, toFlow, withinBudget, type FlowInput } from "./flow";
import type { ResolvedLayers } from "./layers";
import { ROOT, type VisibleEdge, type VisibleNode } from "./model";
import { createStabilizer } from "./stable";

function node(key: string): VisibleNode {
  const apiNode = { key, kind: "dir", name: key.slice(4), path: key.slice(4), meta: {} } as ApiNode;
  return { type: "node", id: key, node: apiNode, parent: null, expanded: false, depth: 0 };
}

function edge(source: string, target: string, kind: EdgeKind, count = 1): VisibleEdge {
  return {
    id: `${source}|${target}|${kind}`,
    source,
    target,
    kind,
    count,
    minConfidence: 1,
    maxConfidence: 1,
  };
}

const KEYS = ["dir:a", "dir:b", "dir:c"];

function input(overrides: Partial<FlowInput> = {}): FlowInput {
  return {
    visible: KEYS.map(node),
    edges: [
      edge("dir:a", "dir:b", "calls", 3),
      edge("dir:b", "dir:c", "imports"),
      edge("dir:c", "dir:a", "tested_by", 5),
    ],
    layouts: new Map([
      [
        ROOT,
        {
          positions: Object.fromEntries(KEYS.map((key, i) => [key, { x: i * 200, y: 0 }])),
          content: { width: 600, height: 50 },
        },
      ],
    ]),
    loading: new Set(),
    selected: null,
    numberOf: createNumbering(),
    childCount: () => undefined,
    fileCount: () => undefined,
    ...overrides,
  };
}

function highlight(edgeId: string): ResolvedLayers {
  return {
    nodes: new Map(),
    edges: new Map([[edgeId, { style: "primary", layers: ["path"] }]]),
    extra: [],
  };
}

describe("toFlow", () => {
  it("leaves out edge kinds the reader switched off, unless a layer highlights them", () => {
    const hiddenKinds = new Set<EdgeKind>(["tested_by"]);
    const plain = toFlow(input({ hiddenKinds }));
    expect(plain.edges.map((e) => e.id)).toEqual(["dir:a|dir:b|calls", "dir:b|dir:c|imports"]);
    expect(plain.edgeCount).toEqual({ total: 2, drawn: 2 });
    const highlighted = toFlow(input({ hiddenKinds, layers: highlight("dir:c|dir:a|tested_by") }));
    expect(highlighted.edges.map((e) => e.id)).toContain("dir:c|dir:a|tested_by");
  });

  it("draws highlighted edges last, so they sit on top of the others", () => {
    const flow = toFlow(input({ layers: highlight("dir:a|dir:b|calls") }));
    expect(flow.edges.at(-1)!.id).toBe("dir:a|dir:b|calls");
    expect(flow.edges.at(-1)!.data!.highlight).toBe("primary");
    expect(flow.edges.slice(0, -1).every((e) => e.data!.dimmed)).toBe(true);
  });

  it("tags each edge with its ends' stable numbers for focus mode", () => {
    const numberOf = createNumbering();
    numberOf("dir:c");
    const flow = toFlow(input({ numberOf }));
    const ends = Object.fromEntries(
      flow.edges.map((e) => [e.id, [e.data!.sourceNo, e.data!.targetNo]]),
    );
    // dir:c was numbered first; the rest in the order the edges met them.
    expect(ends["dir:a|dir:b|calls"]).toEqual([1, 2]);
    expect(ends["dir:c|dir:a|tested_by"]).toEqual([0, 1]);
    // The same keys keep their numbers when the node list changes.
    const fewer = toFlow(input({ numberOf, visible: KEYS.slice(1).map(node) }));
    expect(fewer.edges.map((e) => [e.data!.sourceNo, e.data!.targetNo])).toEqual([[2, 0]]);
  });
});

describe("toFlow with the stabilizer", () => {
  it("keeps unchanged edges as the same objects when other nodes come and go", () => {
    const numberOf = createNumbering();
    const stabilize = createStabilizer();
    const first = stabilize(toFlow(input({ numberOf, visible: KEYS.slice(0, 2).map(node) })));
    const again = stabilize(toFlow(input({ numberOf })));
    // dir:c joined the map; the a→b edge didn't change, so React Flow can skip it.
    expect(again.edges.find((e) => e.id === "dir:a|dir:b|calls")).toBe(first.edges[0]);
    expect(again.edges).toHaveLength(3);
  });
});

describe("createNumbering", () => {
  it("numbers keys in first-seen order and never renumbers them", () => {
    const numberOf = createNumbering();
    expect(["x", "y", "x", "z", "y"].map(numberOf)).toEqual([0, 1, 0, 2, 1]);
  });
});

describe("withinBudget", () => {
  it("keeps everything under the budget, else the kept, then the heaviest edges", () => {
    const edges = [
      edge("dir:a", "dir:b", "calls", 1),
      edge("dir:b", "dir:c", "imports", 9),
      edge("dir:c", "dir:a", "calls", 4),
    ];
    expect(withinBudget(edges, 5, () => 0)).toEqual(edges);
    const keepFirst = (e: VisibleEdge) => (e.id === "dir:a|dir:b|calls" ? 1 : 0);
    expect(withinBudget(edges, 2, keepFirst).map((e) => e.count)).toEqual([1, 9]);
    expect(withinBudget(edges, 2, () => 0).map((e) => e.count)).toEqual([9, 4]);
  });
});
