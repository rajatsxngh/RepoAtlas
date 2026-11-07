import { describe, expect, it } from "vitest";
import pyData from "@/lib/api/mock/fixtures/data/py_small.json";
import { seededRandom, syntheticGraph } from "@/lib/api/mock/fixtures/synthetic";
import { MockGraph, type FixtureData } from "@/lib/api/mock/graph";
import { GraphModel, ROOT, childLevel, isMoreId, moreId } from "./model";

const py = new MockGraph(pyData as unknown as FixtureData);

/** A model over a mock graph that loads children the way the canvas does. */
function setup(graph: MockGraph, cap = 300) {
  const model = new GraphModel(cap);
  model.addSubgraph(graph.subgraph(ROOT, "dir"));
  model.addFileAggregate(graph.subgraph(ROOT, "file"));
  const open = (key: string) => {
    const node = model.nodes.get(key)!;
    if (!model.isLoaded(key)) model.addSubgraph(graph.subgraph(key, childLevel(node)));
    return model.expand(key, { makeRoom: true });
  };
  return { model, open };
}

const ids = (model: GraphModel) => model.visibleNodes().map((v) => v.id);
const edgeIds = (model: GraphModel) =>
  model
    .visibleEdges()
    .map((e) => e.id)
    .sort();

describe("expanding and collapsing", () => {
  it("replaces a folder's aggregated edges by edges to its children", () => {
    const { model, open } = setup(py);
    expect(edgeIds(model)).toContain("dir:tests|dir:src|calls");
    expect(open("dir:src")).toMatchObject({ ok: true });
    expect(ids(model)).toContain("dir:src/shop");
    const edges = edgeIds(model);
    expect(edges).not.toContain("dir:tests|dir:src|calls");
    expect(edges).toContain("dir:tests|dir:src/shop|calls");
    for (const edge of model.visibleEdges()) {
      expect(edge.source === "dir:src" || edge.target === "dir:src").toBe(false);
    }
  });

  it("opens files into symbols and classes into methods, with the edges inside them", () => {
    const { model, open } = setup(py);
    for (const key of ["dir:src", "dir:src/shop", "file:src/shop/cart.py"]) open(key);
    expect(ids(model)).toEqual(
      expect.arrayContaining([
        "py:src/shop/cart.py:Cart:class",
        "py:src/shop/cart.py:GiftCart:class",
      ]),
    );
    expect(edgeIds(model)).toContain(
      "py:src/shop/cart.py:GiftCart:class|py:src/shop/cart.py:Cart:class|inherits",
    );
    open("py:src/shop/cart.py:Cart:class");
    expect(ids(model)).toContain("py:src/shop/cart.py:Cart.add:method");
    const parents = Object.fromEntries(model.visibleNodes().map((v) => [v.id, v.parent]));
    expect(parents["py:src/shop/cart.py:Cart.add:method"]).toBe("py:src/shop/cart.py:Cart:class");
    expect(parents["file:src/shop/cart.py"]).toBe("dir:src/shop");
  });

  it("collapses a container with everything opened inside it", () => {
    const { model, open } = setup(py);
    for (const key of ["dir:src", "dir:src/shop", "file:src/shop/cart.py"]) open(key);
    model.collapse("dir:src");
    expect(ids(model)).toEqual(["dir:scripts", "dir:src", "dir:stubs", "dir:tests", "dir:vendor"]);
    expect([...model.expanded]).toEqual([ROOT]);
    // Reopening shows the folder again, closed inside.
    open("dir:src");
    expect(ids(model)).toContain("dir:src/shop");
    expect(model.expanded.has("dir:src/shop")).toBe(false);
  });

  it("refuses what it can't open", () => {
    const { model, open } = setup(py);
    expect(model.expand("dir:src")).toMatchObject({ ok: false, reason: "not-loaded" });
    model.addSubgraph(py.subgraph("dir:src/shop", "dir"));
    expect(model.expand("dir:src/shop")).toMatchObject({ ok: false, reason: "hidden" });
    open("dir:src");
    expect(open("file:src/shop/cart.py")).toMatchObject({ ok: false, reason: "hidden" });
    open("dir:src/shop");
    expect(open("file:src/shop/__init__.py")).toMatchObject({ ok: false, reason: "empty" });
  });

  it("shows the largest children and a more node when the cap is near", () => {
    const { model, open } = setup(py, 8);
    open("dir:src");
    open("dir:src/shop");
    const visible = model.visibleNodes();
    expect(visible.length).toBeLessThanOrEqual(8);
    const more = visible.find((v) => v.id === moreId("dir:src/shop"));
    expect(more?.type).toBe("more");
    const hidden = more?.type === "more" ? more.hidden : [];
    expect(model.proxyOf(hidden[0]!)).toBe(moreId("dir:src/shop"));
    // Edges to hidden children attach to the more node.
    expect(model.visibleEdges().some((e) => isMoreId(e.source) || isMoreId(e.target))).toBe(true);
  });

  it("shows more children as the cap allows, and closes old containers to make room", () => {
    const { model, open } = setup(py, 9);
    open("dir:src");
    open("dir:src/shop");
    const before = model.visibleCount();
    expect(model.showMore("dir:src/shop")).toBe(0);
    expect(model.visibleCount()).toBe(before);
    model.collapse("dir:src");
    open("dir:tests");
    open("dir:src");
    open("dir:src/shop");
    expect(model.visibleCount()).toBeLessThanOrEqual(9);
    // tests was opened first, so it is closed to make room for shop's children.
    expect(model.expanded.has("dir:tests")).toBe(false);
    expect(model.expanded.has("dir:src/shop")).toBe(true);
  });

  it("never shows more nodes than the cap on a 10,000-node graph", () => {
    const graph = new MockGraph(syntheticGraph());
    const cap = 300;
    const { model, open } = setup(graph, cap);
    const random = seededRandom(99);
    let opened = 0;
    for (let step = 0; step < 400; step += 1) {
      const visible = model.visibleNodes();
      expect(visible.length).toBeLessThanOrEqual(cap);
      const roll = random();
      const candidates = visible.filter(
        (v) => v.type === "node" && !v.expanded && ["dir", "file", "class"].includes(v.node.kind),
      );
      const mores = visible.filter((v) => v.type === "more");
      if (roll < 0.75 && candidates.length) {
        const pick = candidates[Math.floor(random() * candidates.length)]!;
        if (open(pick.id).ok) opened += 1;
      } else if (roll < 0.9 && mores.length) {
        const pick = mores[Math.floor(random() * mores.length)]!;
        if (pick.type === "more") model.showMore(pick.container);
      } else {
        const expanded = [...model.expanded].filter((key) => key !== ROOT);
        if (expanded.length) model.collapse(expanded[Math.floor(random() * expanded.length)]!);
      }
    }
    expect(opened).toBeGreaterThan(100);
    expect(model.visibleCount()).toBeLessThanOrEqual(cap);
  });
});
