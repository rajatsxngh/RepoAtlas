import { describe, expect, it } from "vitest";
import pyData from "@/lib/api/mock/fixtures/data/py_small.json";
import { MockGraph, type FixtureData } from "@/lib/api/mock/graph";
import { edgeKey } from "@/lib/graph/keys";
import { resolveLayers, type HighlightLayer } from "./layers";
import { GraphModel, ROOT, childLevel, moreId } from "./model";

const py = new MockGraph(pyData as unknown as FixtureData);

function model(cap = 300, open: string[] = []) {
  const m = new GraphModel(cap);
  m.addSubgraph(py.subgraph(ROOT, "dir"));
  m.addFileAggregate(py.subgraph(ROOT, "file"));
  for (const key of open) {
    if (!m.isLoaded(key)) m.addSubgraph(py.subgraph(key, childLevel(m.nodes.get(key)!)));
    m.expand(key);
  }
  return m;
}

function resolve(m: GraphModel, layers: HighlightLayer[]) {
  const edges = new Set(m.visibleEdges().map((edge) => edge.id));
  return resolveLayers(layers, (key) => m.proxyOf(key), edges);
}

const ADD = "py:src/shop/cart.py:Cart.add:method";
const TOTAL = "py:src/shop/cart.py:Cart.total:method";
const ROUND = "py:src/shop/utils/helpers.py:round_money:function";

describe("highlight layers", () => {
  it("bubbles hidden nodes up to the nearest visible ancestor with the max score and a count", () => {
    const m = model();
    const resolved = resolve(m, [
      {
        id: "impact",
        nodeKeys: [ADD, TOTAL, ROUND],
        style: "warning",
        scores: { [ADD]: 0.4, [TOTAL]: 0.9, [ROUND]: 0.2 },
      },
    ]);
    // Everything lives under src, which is collapsed.
    expect([...resolved.nodes.keys()]).toEqual(["dir:src"]);
    expect(resolved.nodes.get("dir:src")).toMatchObject({
      style: "warning",
      score: 0.9,
      hidden: 3,
      direct: false,
      layers: ["impact"],
    });
  });

  it("lands on deeper ancestors as containers open, and on the node itself once visible", () => {
    const layer: HighlightLayer = {
      id: "l",
      nodeKeys: [ADD, ROUND],
      style: "primary",
      scores: { [ADD]: 0.7 },
    };
    const opened = model(300, ["dir:src", "dir:src/shop"]);
    const partial = resolve(opened, [layer]);
    expect(partial.nodes.get("file:src/shop/cart.py")).toMatchObject({ hidden: 1, score: 0.7 });
    expect(partial.nodes.get("dir:src/shop/utils")).toMatchObject({ hidden: 1, score: 1 });
    expect(partial.nodes.has("dir:src")).toBe(false);

    const deep = model(300, [
      "dir:src",
      "dir:src/shop",
      "file:src/shop/cart.py",
      "py:src/shop/cart.py:Cart:class",
    ]);
    expect(resolve(deep, [layer]).nodes.get(ADD)).toMatchObject({
      direct: true,
      hidden: 0,
      score: 0.7,
    });
  });

  it("bubbles into a +N more node when the container hides the node", () => {
    const m = model(8, ["dir:src", "dir:src/shop"]);
    const hidden = m.hiddenIn("dir:src/shop");
    expect(hidden.length).toBeGreaterThan(0);
    const resolved = resolve(m, [{ id: "l", nodeKeys: hidden, style: "secondary" }]);
    expect(resolved.nodes.get(moreId("dir:src/shop"))).toMatchObject({ hidden: hidden.length });
  });

  it("composes several layers: the most important style wins and all are listed", () => {
    const m = model();
    const resolved = resolve(m, [
      { id: "a", nodeKeys: ["dir:src", ADD], style: "secondary", scores: { "dir:src": 0.3 } },
      { id: "b", nodeKeys: [TOTAL], style: "primary", scores: { [TOTAL]: 0.6 } },
      { id: "c", nodeKeys: ["dir:tests"], style: "muted" },
    ]);
    expect(resolved.nodes.get("dir:src")).toMatchObject({
      style: "primary",
      styles: ["primary", "secondary"],
      layers: ["a", "b"],
      direct: true,
      hidden: 2,
      score: 1,
    });
    expect(resolved.nodes.get("dir:tests")).toMatchObject({ style: "muted", direct: true });
  });

  it("puts layer edges on the visible edge between stand-ins, or adds an extra edge", () => {
    const m = model();
    const test = "py:tests/test_cart.py:test_add_rounds_prices:test";
    const resolved = resolve(m, [
      {
        id: "path",
        nodeKeys: [test, ADD],
        edgeKeys: [edgeKey(test, ADD, "calls")],
        style: "primary",
      },
    ]);
    expect(resolved.edges.get("dir:tests|dir:src|calls")).toEqual({
      style: "primary",
      layers: ["path"],
    });
    expect(resolved.extra).toEqual([]);

    const open = model(300, [
      "dir:src",
      "dir:src/shop",
      "file:src/shop/cart.py",
      "py:src/shop/cart.py:Cart:class",
    ]);
    const inside = resolve(open, [
      {
        id: "p",
        nodeKeys: [ADD, ROUND],
        edgeKeys: [edgeKey(ADD, ROUND, "calls"), edgeKey(ADD, ADD, "calls"), "junk"],
        style: "primary",
      },
    ]);
    // add (visible) calls round_money (inside the collapsed utils folder): no edge is drawn
    // between them at that granularity, so the layer adds one.
    expect(inside.extra).toEqual([
      {
        id: `layer:${ADD}|dir:src/shop/utils|calls`,
        source: ADD,
        target: "dir:src/shop/utils",
        kind: "calls",
        style: "primary",
        layers: ["p"],
      },
    ]);
  });

  it("ignores keys the map can't place", () => {
    const resolved = resolve(model(), [
      { id: "x", nodeKeys: ["py:nowhere.py:ghost:function"], style: "primary" },
    ]);
    expect(resolved.nodes.size).toBe(0);
  });
});
