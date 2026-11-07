/** Rectangles on the map: where floating edges meet node borders. */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export function center(rect: Rect): Point {
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

/** Where the ray from the rectangle's center toward `toward` leaves the rectangle. */
export function borderPoint(rect: Rect, toward: Point): Point {
  const c = center(rect);
  const dx = toward.x - c.x;
  const dy = toward.y - c.y;
  if (dx === 0 && dy === 0) return c;
  const halfW = rect.width / 2;
  const halfH = rect.height / 2;
  const scale = Math.min(
    dx === 0 ? Number.POSITIVE_INFINITY : halfW / Math.abs(dx),
    dy === 0 ? Number.POSITIVE_INFINITY : halfH / Math.abs(dy),
  );
  return { x: c.x + dx * scale, y: c.y + dy * scale };
}

export function contains(outer: Rect, inner: Rect): boolean {
  return (
    inner.x >= outer.x &&
    inner.y >= outer.y &&
    inner.x + inner.width <= outer.x + outer.width &&
    inner.y + inner.height <= outer.y + outer.height
  );
}

/**
 * End points of a straight edge between two boxes, on their borders. When one box holds the
 * other (a container and something inside it), the edge leaves the inner box toward the outer
 * box's top border instead of its hidden center.
 */
export function floatingEnds(source: Rect, target: Rect): { from: Point; to: Point } {
  if (contains(source, target)) {
    const to = borderPoint(target, { x: center(target).x, y: source.y });
    return { from: { x: to.x, y: source.y }, to };
  }
  if (contains(target, source)) {
    const from = borderPoint(source, { x: center(source).x, y: target.y });
    return { from, to: { x: from.x, y: target.y } };
  }
  return { from: borderPoint(source, center(target)), to: borderPoint(target, center(source)) };
}
