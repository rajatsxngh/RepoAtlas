import { describe, expect, it } from "vitest";
import {
  ROOT_KEY,
  derivedAncestors,
  edgeKey,
  encodeKeySegment,
  fileKeyOf,
  parentDirKey,
  parseEdgeKey,
  parseKey,
} from "./keys";

describe("parseKey", () => {
  it("parses every key form", () => {
    expect(parseKey("dir:.")).toEqual({ type: "dir", path: "." });
    expect(parseKey("dir:src/shop")).toEqual({ type: "dir", path: "src/shop" });
    expect(parseKey("file:src/shop/app.py")).toEqual({ type: "file", path: "src/shop/app.py" });
    expect(parseKey("route:src/server.ts:GET /users/%3Aid")).toEqual({
      type: "route",
      path: "src/server.ts",
      signature: "GET /users/:id",
    });
    expect(parseKey("py:src/shop/cart.py:Cart.add:method")).toEqual({
      type: "symbol",
      lang: "py",
      path: "src/shop/cart.py",
      qualname: "Cart.add",
      kind: "method",
      n: 1,
    });
    expect(parseKey("ts:src/a.ts:<anon>:function~3")).toMatchObject({ kind: "function", n: 3 });
    expect(parseKey("ts:src/a%3Ab.ts:x:function")).toMatchObject({ path: "src/a:b.ts" });
  });

  it("rejects malformed keys", () => {
    for (const bad of ["", "dir:", "file:", "nope", "go:a.go:x:function", "py:a.py:function"]) {
      expect(parseKey(bad)).toBeNull();
    }
  });
});

describe("derived ancestors", () => {
  it("finds a symbol's file and directories up to the root", () => {
    expect(fileKeyOf("py:src/shop/cart.py:Cart.add:method")).toBe("file:src/shop/cart.py");
    expect(fileKeyOf("route:src/server.ts:GET /")).toBe("file:src/server.ts");
    expect(fileKeyOf("file:a.py")).toBe("file:a.py");
    expect(fileKeyOf("dir:src")).toBeNull();
    expect(derivedAncestors("py:src/shop/cart.py:Cart.add:method")).toEqual([
      "file:src/shop/cart.py",
      "dir:src/shop",
      "dir:src",
      ROOT_KEY,
    ]);
    expect(derivedAncestors("file:setup.py")).toEqual([ROOT_KEY]);
    expect(derivedAncestors("dir:src/shop")).toEqual(["dir:src", ROOT_KEY]);
    expect(derivedAncestors(ROOT_KEY)).toEqual([]);
    expect(parentDirKey("top.py")).toBe(ROOT_KEY);
  });

  it("keeps percent-encoded parts as they are", () => {
    expect(derivedAncestors("py:we%3Aird/x.py:f:function")).toEqual([
      "file:we%3Aird/x.py",
      "dir:we%3Aird",
      ROOT_KEY,
    ]);
  });
});

describe("edge keys", () => {
  it("round-trips, even with | inside paths", () => {
    const src = "py:a|b.py:f:function";
    const dst = "file:c.py";
    expect(parseEdgeKey(edgeKey(src, dst, "calls"))).toEqual({ src, dst, kind: "calls" });
    expect(parseEdgeKey("file:a.py|file:b.py|imports")).toEqual({
      src: "file:a.py",
      dst: "file:b.py",
      kind: "imports",
    });
    expect(parseEdgeKey("garbage")).toBeNull();
  });

  it("encodes a key as one path segment", () => {
    expect(encodeKeySegment("py:src/a.py:f:function")).toBe("py%3Asrc%2Fa.py%3Af%3Afunction");
    expect(encodeKeySegment("ts:x%3Ay.ts:f:function")).toContain("%253A");
  });
});
