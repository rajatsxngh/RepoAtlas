import { describe, expect, it } from "vitest";
import pyData from "@/lib/api/mock/fixtures/data/py_small.json";
import tsData from "@/lib/api/mock/fixtures/data/ts_small.json";
import { syntheticGraph } from "@/lib/api/mock/fixtures/synthetic";
import { MockGraph, type FixtureData } from "@/lib/api/mock/graph";
import { GraphModel, ROOT, isMoreId, moreId } from "./model";

const py = new MockGraph(pyData as unknown as FixtureData);
const ts = new MockGraph(tsData as unknown as FixtureData);

function rootModel(graph: MockGraph, cap = 300): GraphModel {
  const model = new GraphModel(cap);
  model.addSubgraph(graph.subgraph(ROOT, "dir"));
  return model;
}

const sortEdges = (edges: { id: string; count: number }[]) =>
  edges.map((e) => `${e.id}#${e.count}`).sort();

describe("GraphModel at the top level", () => {
  it("shows the root's children with the root subgraph's edges", () => {
    const model = rootModel(py);
    const visible = model.visibleNodes();
    expect(visible.map((v) => v.id)).toEqual([
      "dir:scripts",
      "dir:src",
      "dir:stubs",
      "dir:tests",
      "dir:vendor",
    ]);
    expect(visible.every((v) => v.parent === null && v.type === "node")).toBe(true);
    const edges = model.visibleEdges();
    expect(edges.length).toBeGreaterThan(0);
    for (const edge of edges) {
      expect(model.isVisible(edge.source) && model.isVisible(edge.target)).toBe(true);
    }
  });

  it.each([
    ["py_small", py],
    ["ts_small", ts],
    ["synthetic", new MockGraph(syntheticGraph({ nodes: 3000 }))],
  ])("lifts the file-level edges of %s to exactly the root subgraph's edges", (_name, graph) => {
    const before = rootModel(graph);
    const rootEdges = sortEdges(before.visibleEdges());
    const after = rootModel(graph);
    after.addFileAggregate(graph.subgraph(ROOT, "file"));
    expect(after.hasFileEdges).toBe(true);
    expect(sortEdges(after.visibleEdges())).toEqual(rootEdges);
    const expected = graph
      .subgraph(ROOT, "dir")
      .edges.map((e) => `${e.src}|${e.dst}|${e.kind}#${e.count}`)
      .sort();
    expect(rootEdges).toEqual(expected);
  });

  it("caps the top level, keeping the largest children and a more node", () => {
    const model = rootModel(py, 3);
    model.addFileAggregate(py.subgraph(ROOT, "file"));
    const visible = model.visibleNodes();
    expect(visible).toHaveLength(3);
    const more = visible.at(-1)!;
    expect(more).toMatchObject({ type: "more", id: moreId(ROOT), container: ROOT });
    expect(more.type === "more" && more.hidden.length).toBe(3);
    // src holds the most code.
    expect(visible.map((v) => v.id)).toContain("dir:src");
  });

  it("finds the visible stand-in of any node", () => {
    const model = rootModel(py);
    expect(model.proxyOf("dir:src")).toBe("dir:src");
    expect(model.proxyOf("dir:src/shop/utils")).toBe("dir:src");
    expect(model.proxyOf("file:src/shop/cart.py")).toBe("dir:src");
    expect(model.proxyOf("py:src/shop/cart.py:Cart.add:method")).toBe("dir:src");
    expect(model.proxyOf("route:src/shop/app.py:GET /carts/{cart_id}")).toBe("dir:src");
    expect(model.proxyOf(ROOT)).toBeNull();
    const capped = rootModel(py, 2);
    const hidden = capped.visibleNodes().find((v) => v.type === "more");
    expect(hidden?.type === "more" && hidden.hidden.length).toBeGreaterThan(0);
    const stand = hidden?.type === "more" ? capped.proxyOf(hidden.hidden[0]!) : null;
    expect(stand && isMoreId(stand)).toBe(true);
  });

  it("sizes directories by the code below them", () => {
    const model = rootModel(py);
    model.addFileAggregate(py.subgraph(ROOT, "file"));
    expect(model.sizeOf("dir:src")).toBeGreaterThan(model.sizeOf("dir:vendor"));
    expect(model.sizeOf("file:src/shop/cart.py")).toBeGreaterThan(1);
  });

  it("knows ancestors from loaded parents and from keys", () => {
    const model = rootModel(py);
    expect(model.ancestorsOf("py:src/shop/cart.py:Cart.add:method")).toEqual([
      "file:src/shop/cart.py",
      "dir:src/shop",
      "dir:src",
      ROOT,
    ]);
    model.addSubgraph(py.subgraph("file:src/shop/cart.py", "symbol"));
    model.addSubgraph(py.subgraph("py:src/shop/cart.py:Cart:class", "symbol"));
    expect(model.ancestorsOf("py:src/shop/cart.py:Cart.add:method")).toEqual([
      "py:src/shop/cart.py:Cart:class",
      "file:src/shop/cart.py",
      "dir:src/shop",
      "dir:src",
      ROOT,
    ]);
  });
});
