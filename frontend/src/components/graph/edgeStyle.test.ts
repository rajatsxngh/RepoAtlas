import { describe, expect, it } from "vitest";
import { aspectRatioFor } from "@/workers/layoutEngine";
import { PARALLEL_SPACING, bundle, edgeTitle, isGuess, strokeWidth } from "./edgeStyle";

describe("edge style", () => {
  it("dashes only bundles where every edge is a guess", () => {
    expect(isGuess({ maxConfidence: 0.45 })).toBe(true);
    expect(isGuess({ maxConfidence: 0.6 })).toBe(false);
    expect(isGuess({ maxConfidence: 1 })).toBe(false);
  });

  it("widens with the count, logarithmically and up to a cap", () => {
    const widths = [1, 2, 10, 100, 10_000].map(strokeWidth);
    expect(widths).toEqual([...widths].sort((a, b) => a - b));
    expect(widths[0]).toBeCloseTo(1.3);
    expect(widths.at(-1)).toBe(4.5);
  });

  it("describes a bundle for its tooltip", () => {
    expect(edgeTitle({ kind: "calls", count: 12, minConfidence: 0.45, maxConfidence: 1 })).toBe(
      "12 calls, confidence 0.45–1",
    );
    expect(edgeTitle({ kind: "imports", count: 1, minConfidence: 0.4, maxConfidence: 0.4 })).toBe(
      "1 import, confidence 0.40 (guessed by name)",
    );
    expect(edgeTitle({ kind: "tested_by", count: 1200, minConfidence: 1, maxConfidence: 1 })).toBe(
      "1,200 test links, confidence 1",
    );
  });

  it("spreads parallel edges of a pair evenly, whichever way they point", () => {
    const { offsets, labelled } = bundle([
      { id: "a|b|calls", source: "a", target: "b", count: 3 },
      { id: "b|a|calls", source: "b", target: "a", count: 9 },
      { id: "a|b|imports", source: "a", target: "b", count: 1 },
      { id: "a|c|calls", source: "a", target: "c", count: 2 },
    ]);
    const pair = ["a|b|calls", "a|b|imports", "b|a|calls"].map((id) => offsets.get(id));
    expect(pair).toEqual([-PARALLEL_SPACING, 0, PARALLEL_SPACING]);
    expect(offsets.get("a|c|calls")).toBe(0);
    expect([...labelled].sort()).toEqual(["a|c|calls", "b|a|calls"]);
  });

  it("packs few children wide and many children closer to the screen's shape", () => {
    expect(aspectRatioFor(3)).toBe(8);
    expect(aspectRatioFor(50)).toBeLessThan(aspectRatioFor(10));
    expect(aspectRatioFor(400)).toBe(1.8);
  });
});
