import { describe, expect, it } from "vitest";
import pyExpected from "./fixtures/data/py_small.expected.json";
import pyData from "./fixtures/data/py_small.json";
import tsExpected from "./fixtures/data/ts_small.expected.json";
import tsData from "./fixtures/data/ts_small.json";
import { MockApiError, MockGraph, trigramSimilarity, type FixtureData } from "./graph";
import { xssFixture } from "./fixtures/xss";
import { syntheticOwners } from "./owners";

interface Expected {
  subgraphs: {
    parent: string;
    level: "dir" | "file" | "symbol";
    nodes: string[];
    edges: unknown[];
  }[];
  neighbors: {
    key: string;
    direction: "in" | "out" | "both";
    kinds: ("calls" | null)[] | null;
    depth: number;
    nodes: [string, number][];
    edge_count: number;
  }[];
  paths: { from: string; to: string; length: number | null; directed: boolean | null }[];
}

const CASES = [
  ["py_small", pyData, pyExpected],
  ["ts_small", tsData, tsExpected],
] as unknown as [string, FixtureData, Expected][];

describe.each(CASES)("mock graph matches the API on %s", (_name, data, expected) => {
  const graph = new MockGraph(data);

  it("aggregates every subgraph exactly like GraphStore", () => {
    for (const want of expected.subgraphs) {
      const got = graph.subgraph(want.parent, want.level);
      expect(
        got.nodes.map((node) => node.key),
        `${want.level} ${want.parent}`,
      ).toEqual(want.nodes);
      expect(got.edges, `${want.level} ${want.parent}`).toEqual(want.edges);
    }
  });

  it("traverses like the in-memory adjacency", () => {
    for (const want of expected.neighbors) {
      const got = graph.neighbors(
        want.key,
        want.direction,
        want.kinds as "calls"[] | null,
        want.depth,
      );
      const label = `${want.direction} ${want.kinds} ${want.key}`;
      expect(
        got.nodes.map((hop) => [hop.node.key, hop.depth]),
        label,
      ).toEqual(want.nodes);
      expect(got.edges.length, label).toBe(want.edge_count);
    }
  });

  it("finds shortest paths of the same length and direction", () => {
    for (const want of expected.paths) {
      const label = `${want.from} -> ${want.to}`;
      if (want.length === null) {
        expect(() => graph.path(want.from, want.to, null), label).toThrow(MockApiError);
        continue;
      }
      const got = graph.path(want.from, want.to, null);
      expect([got.nodes.length, got.directed], label).toEqual([want.length, want.directed]);
      got.edges.forEach((edge, index) => {
        const ends = [got.nodes[index]!.key, got.nodes[index + 1]!.key];
        expect(ends.sort(), label).toEqual([edge.src, edge.dst].sort());
      });
    }
  });
});

describe("mock graph reads", () => {
  const graph = new MockGraph({
    ...(pyData as unknown as FixtureData),
    owners: (path) => syntheticOwners(path),
  });

  it("refuses levels that don't fit the parent", () => {
    expect(() => graph.subgraph("file:src/shop/app.py", "dir")).toThrow(/needs a directory/);
    expect(() => graph.subgraph("dir:src", "symbol")).toThrow(/not a directory/);
    expect(() => graph.subgraph("dir:nope", "dir")).toThrow(MockApiError);
  });

  it("locates the innermost symbol, else the file", () => {
    expect(graph.locate("src/shop/cart.py", 14).key).toBe("py:src/shop/cart.py:Cart.add:method");
    expect(graph.locate("src/shop/cart.py", 1).key).toBe("file:src/shop/cart.py");
    expect(() => graph.locate("src/shop/cart.py", 0)).toThrow(/start at 1/);
    expect(() => graph.locate("src/shop/cart.py", 999)).toThrow(/past the end/);
    expect(() => graph.locate("../etc/passwd", 1)).toThrow(/not a file/);
  });

  it("reads code with the API's clamping", () => {
    const slice = graph.code("src/shop/cart.py", 14, 15);
    expect(slice).toMatchObject({ path: "src/shop/cart.py", start: 14, end: 15 });
    expect(slice.text.split("\n")).toHaveLength(2);
    expect(graph.code("src/shop/cart.py", -5, 2).start).toBe(1);
    const whole = graph.code("src/shop/cart.py", 1, null);
    expect(whole.end).toBe(whole.line_count);
    expect(() => graph.code("src/shop/cart.py", 5, 4)).toThrow(/ends before/);
    expect(() => graph.code("src/shop/cart.py", 999, 1000)).toThrow(/past the end/);
    expect(() => graph.code("vendor/lib.py", 1, 2)).toThrow(/skipped/);
  });

  it("ranks exact, then prefix, then fuzzy matches", () => {
    const results = graph.search("cart", null, 20);
    expect(results[0]).toMatchObject({ match: "exact" });
    const order = { exact: 0, prefix: 1, fuzzy: 2 };
    const ranks = results.map((r) => order[r.match]);
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    expect(graph.search("Cart.ad", ["method"], 5)[0]!.node.key).toBe(
      "py:src/shop/cart.py:Cart.add:method",
    );
    expect(graph.search("formt_price", null, 5)[0]).toMatchObject({ match: "fuzzy" });
    expect(() => graph.search("  ", null, 5)).toThrow(/empty/);
    expect(() => graph.search("x", null, 51)).toThrow(/limit/);
  });

  it("counts degree by kind and hides owners of directories", () => {
    const detail = graph.nodeDetail("py:src/shop/cart.py:Cart:class");
    expect(detail.degree.outgoing.contains).toBeGreaterThan(0);
    expect(detail.owners.length).toBeGreaterThan(0);
    expect(graph.nodeDetail("dir:src").owners).toEqual([]);
  });

  it("computes pg_trgm-like similarity", () => {
    expect(trigramSimilarity("helper", "helper")).toBe(1);
    expect(trigramSimilarity("helper", "helpr")).toBeGreaterThan(0.3);
    expect(trigramSimilarity("helper", "zzz")).toBe(0);
  });

  it("keeps the XSS fixture's payloads in names, owners, and code", () => {
    const xss = new MockGraph(xssFixture());
    const root = xss.subgraph("dir:.", "dir");
    expect(root.nodes.map((node) => node.name).join(" ")).toContain("<img src=x onerror=");
    const file = root.nodes.find((node) => node.kind === "dir")!;
    const inner = xss.subgraph(file.key, "dir").nodes[0]!;
    expect(inner.name).toBe("<svg onload=alert('file')>.py");
    expect(xss.code(inner.path, 1, 1).text).toContain("<script>");
    expect(
      xss
        .nodeDetail(inner.key)
        .owners.map((o) => o.author_name)
        .join(),
    ).toContain("&lt;img");
  });
});
