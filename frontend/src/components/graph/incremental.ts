/**
 * Incremental layout. When a child changes size (it was expanded or collapsed), its container is
 * not laid out again: the siblings beyond it move by the size change, so everything keeps its
 * place relative to everything else and the map doesn't jump.
 *
 * - Growing: siblings whose leading edge lies beyond the child's old trailing edge move by the
 *   growth. Order along each axis is kept, so boxes that didn't overlap still don't.
 * - Shrinking: the same siblings move back together, by as much as the closest box in their way
 *   allows (keeping a gap), so the space closes without creating overlaps.
 */

import type { Size } from "./sizes";

export interface Placement {
  positions: Readonly<Record<string, { x: number; y: number }>>;
  sizes: Readonly<Record<string, Size>>;
  content: Size;
}

export const GAP = 24;

type Axis = "x" | "y";
const DIM: Record<Axis, "width" | "height"> = { x: "width", y: "height" };
const CROSS: Record<Axis, Axis> = { x: "y", y: "x" };

/** Extent of the children: the farthest right and bottom edges. */
export function extent(
  positions: Readonly<Record<string, { x: number; y: number }>>,
  sizes: Readonly<Record<string, Size>>,
): Size {
  let width = 0;
  let height = 0;
  for (const [id, at] of Object.entries(positions)) {
    const size = sizes[id];
    if (!size) continue;
    width = Math.max(width, at.x + size.width);
    height = Math.max(height, at.y + size.height);
  }
  return { width: Math.ceil(width), height: Math.ceil(height) };
}

function overlapsAcross(
  a: { x: number; y: number },
  aSize: Size,
  b: { x: number; y: number },
  bSize: Size,
  axis: Axis,
): boolean {
  const cross = CROSS[axis];
  const dim = DIM[cross];
  return a[cross] < b[cross] + bSize[dim] + GAP && b[cross] < a[cross] + aSize[dim] + GAP;
}

function adjust(
  positions: Record<string, { x: number; y: number }>,
  sizes: Readonly<Record<string, Size>>,
  id: string,
  previous: Size,
  axis: Axis,
): Record<string, { x: number; y: number }> {
  const at = positions[id]!;
  const dim = DIM[axis];
  const delta = sizes[id]![dim] - previous[dim];
  if (delta === 0) return positions;
  const edge = at[axis] + previous[dim];
  const movers = Object.keys(positions).filter(
    (other) => other !== id && positions[other]![axis] >= edge,
  );
  if (!movers.length) return positions;
  let shift = delta;
  if (delta < 0) {
    const stayers = Object.keys(positions).filter((other) => !movers.includes(other));
    let allowed = -delta;
    for (const mover of movers) {
      const m = positions[mover]!;
      const mSize = sizes[mover]!;
      for (const stayer of stayers) {
        const s = positions[stayer]!;
        const sSize = sizes[stayer]!;
        if (s[axis] + sSize[dim] > m[axis]) continue;
        if (!overlapsAcross(m, mSize, s, sSize, axis)) continue;
        allowed = Math.min(allowed, Math.max(0, m[axis] - (s[axis] + sSize[dim]) - GAP));
      }
    }
    shift = -allowed;
  }
  if (shift === 0) return positions;
  const next = { ...positions };
  for (const mover of movers) next[mover] = { ...next[mover]!, [axis]: next[mover]![axis] + shift };
  return next;
}

/** The placement after child `id` takes size `next` (other children keep theirs). */
export function resizeChild(placement: Placement, id: string, next: Size): Placement {
  const previous = placement.sizes[id];
  if (!previous || !placement.positions[id]) return placement;
  if (previous.width === next.width && previous.height === next.height) return placement;
  const sizes = { ...placement.sizes, [id]: next };
  let positions: Record<string, { x: number; y: number }> = { ...placement.positions };
  positions = adjust(positions, sizes, id, previous, "y");
  positions = adjust(positions, sizes, id, previous, "x");
  return { positions, sizes, content: extent(positions, sizes) };
}
