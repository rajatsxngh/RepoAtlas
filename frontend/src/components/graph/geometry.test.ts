import { describe, expect, it } from "vitest";
import { borderPoint, contains, floatingEnds } from "./geometry";

const box = { x: 0, y: 0, width: 100, height: 40 };

describe("geometry", () => {
  it("finds where a ray from the center leaves a box", () => {
    expect(borderPoint(box, { x: 50, y: 500 })).toEqual({ x: 50, y: 40 });
    expect(borderPoint(box, { x: 500, y: 20 })).toEqual({ x: 100, y: 20 });
    expect(borderPoint(box, { x: 50, y: 20 })).toEqual({ x: 50, y: 20 });
    // Toward (150, 70) the ray hits the bottom edge first: scale min(50/100, 20/50) = 0.4.
    expect(borderPoint(box, { x: 150, y: 70 })).toEqual({ x: 90, y: 40 });
  });

  it("connects two boxes border to border", () => {
    const below = { x: 0, y: 100, width: 100, height: 40 };
    expect(floatingEnds(box, below)).toEqual({ from: { x: 50, y: 40 }, to: { x: 50, y: 100 } });
  });

  it("leaves a nested box toward its container's top", () => {
    const outer = { x: 0, y: 0, width: 300, height: 200 };
    const inner = { x: 20, y: 60, width: 100, height: 40 };
    expect(contains(outer, inner)).toBe(true);
    expect(floatingEnds(inner, outer)).toEqual({ from: { x: 70, y: 60 }, to: { x: 70, y: 0 } });
    expect(floatingEnds(outer, inner).to).toEqual({ x: 70, y: 60 });
  });
});
