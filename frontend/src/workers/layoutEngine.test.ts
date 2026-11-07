import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";
import { containerSize } from "@/components/graph/sizes";
import { layoutContainers } from "./layoutEngine";

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
