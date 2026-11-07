import { describe, expect, it } from "vitest";
import { seededRandom } from "@/lib/api/mock/fixtures/synthetic";
import { extent, resizeChild, type Placement } from "./incremental";

function overlapping(placement: Placement): [string, string] | null {
  const ids = Object.keys(placement.positions);
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const a = placement.positions[ids[i]!]!;
      const b = placement.positions[ids[j]!]!;
      const as = placement.sizes[ids[i]!]!;
      const bs = placement.sizes[ids[j]!]!;
      if (
        a.x < b.x + bs.width &&
        b.x < a.x + as.width &&
        a.y < b.y + bs.height &&
        b.y < a.y + as.height
      ) {
        return [ids[i]!, ids[j]!];
      }
    }
  }
  return null;
}

/** A grid of boxes with irregular sizes and gaps, like a laid-out container. */
function grid(random: () => number, rows: number, cols: number): Placement {
  const positions: Record<string, { x: number; y: number }> = {};
  const sizes: Record<string, { width: number; height: number }> = {};
  let y = 0;
  for (let r = 0; r < rows; r += 1) {
    let x = Math.floor(random() * 40);
    let rowHeight = 0;
    for (let c = 0; c < cols; c += 1) {
      const id = `n${r}_${c}`;
      const size = {
        width: 120 + Math.floor(random() * 140),
        height: 44 + Math.floor(random() * 120),
      };
      positions[id] = { x, y: y + Math.floor(random() * 20) };
      sizes[id] = size;
      x += size.width + 24 + Math.floor(random() * 30);
      rowHeight = Math.max(rowHeight, size.height + 20);
    }
    y += rowHeight + 30;
  }
  return { positions, sizes, content: extent(positions, sizes) };
}

describe("resizeChild", () => {
  it("moves siblings right of and below a growing child, and nothing else", () => {
    const placement: Placement = {
      positions: {
        a: { x: 0, y: 0 },
        b: { x: 200, y: 0 },
        c: { x: 0, y: 100 },
        d: { x: 200, y: 100 },
      },
      sizes: {
        a: { width: 150, height: 44 },
        b: { width: 150, height: 44 },
        c: { width: 150, height: 44 },
        d: { width: 150, height: 44 },
      },
      content: { width: 350, height: 144 },
    };
    const grown = resizeChild(placement, "a", { width: 400, height: 300 });
    expect(grown.positions).toEqual({
      a: { x: 0, y: 0 },
      b: { x: 450, y: 0 },
      c: { x: 0, y: 356 },
      d: { x: 450, y: 356 },
    });
    expect(grown.content).toEqual({ width: 600, height: 400 });
    expect(overlapping(grown)).toBeNull();
  });

  it("closes the space again when the child shrinks back", () => {
    const placement: Placement = {
      positions: { a: { x: 0, y: 0 }, b: { x: 200, y: 0 }, c: { x: 0, y: 100 } },
      sizes: {
        a: { width: 150, height: 44 },
        b: { width: 150, height: 44 },
        c: { width: 150, height: 44 },
      },
      content: { width: 350, height: 144 },
    };
    const grown = resizeChild(placement, "a", { width: 500, height: 400 });
    const back = resizeChild(grown, "a", { width: 150, height: 44 });
    expect(back.positions).toEqual(placement.positions);
    expect(back.content).toEqual(placement.content);
  });

  it("never creates overlaps, growing or shrinking any child", () => {
    const random = seededRandom(7);
    for (let round = 0; round < 60; round += 1) {
      let placement = grid(random, 3 + (round % 4), 2 + (round % 5));
      expect(overlapping(placement)).toBeNull();
      const ids = Object.keys(placement.positions);
      for (let step = 0; step < 6; step += 1) {
        const id = ids[Math.floor(random() * ids.length)]!;
        const current = placement.sizes[id]!;
        const next = {
          width: Math.max(80, Math.round(current.width * (0.3 + random() * 2.5))),
          height: Math.max(40, Math.round(current.height * (0.3 + random() * 2.5))),
        };
        placement = resizeChild(placement, id, next);
        expect(overlapping(placement), `round ${round} step ${step}`).toBeNull();
      }
    }
  });

  it("ignores unknown children and unchanged sizes", () => {
    const placement = grid(seededRandom(1), 2, 2);
    expect(resizeChild(placement, "nope", { width: 1, height: 1 })).toBe(placement);
    expect(resizeChild(placement, "n0_0", placement.sizes.n0_0!)).toBe(placement);
  });
});
