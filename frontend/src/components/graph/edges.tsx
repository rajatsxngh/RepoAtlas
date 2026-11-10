"use client";

import {
  EdgeLabelRenderer,
  useInternalNode,
  useStore,
  type EdgeProps,
  type InternalNode,
} from "@xyflow/react";
import { memo } from "react";
import { EDGE_KINDS, type EdgeKind } from "@/lib/api/types";
import { GUESS_DASH, edgeTitle, isGuess, strokeWidth } from "./edgeStyle";
import type { HighlightStyle } from "./layers";

const HIGHLIGHT_STYLES: HighlightStyle[] = ["primary", "secondary", "warning", "muted"];

export const HIGHLIGHT_COLOR: Record<HighlightStyle, string> = {
  primary: "var(--ra-hl-primary)",
  secondary: "var(--ra-hl-secondary)",
  warning: "var(--ra-hl-warning)",
  muted: "var(--ra-hl-muted)",
};
import type { MapFlowEdge } from "./flow";
import { floatingEnds, type Point, type Rect } from "./geometry";

export function edgeColor(kind: EdgeKind): string {
  return `var(--ra-edge-${kind.replace("_", "-")})`;
}

export function markerId(kind: EdgeKind): string {
  return `ra-arrow-${kind}`;
}

function rectOf(node: InternalNode): Rect {
  return {
    x: node.internals.positionAbsolute.x,
    y: node.internals.positionAbsolute.y,
    width: node.measured.width ?? node.width ?? 0,
    height: node.measured.height ?? node.height ?? 0,
  };
}

/** Shifts a segment sideways along the normal of the pair's canonical direction. */
function shift(from: Point, to: Point, offset: number, flipped: boolean): [Point, Point] {
  if (!offset) return [from, to];
  const dx = flipped ? from.x - to.x : to.x - from.x;
  const dy = flipped ? from.y - to.y : to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  const nx = (-dy / length) * offset;
  const ny = (dx / length) * offset;
  return [
    { x: from.x + nx, y: from.y + ny },
    { x: to.x + nx, y: to.y + ny },
  ];
}

export const MapEdge = memo(function MapEdge({ source, target, data }: EdgeProps<MapFlowEdge>) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  // Counts read from mid zoom; zoomed far out they'd be hundreds of tiny elements to keep
  // current, unless the map is sparse (alwaysCount).
  const near = useStore((state) => state.transform[2] >= 0.5);
  if (!sourceNode || !targetNode || !data) return null;
  const ends = floatingEnds(rectOf(sourceNode), rectOf(targetNode));
  const [from, to] = shift(ends.from, ends.to, data.offset, source > target);
  const path = `M ${from.x},${from.y} L ${to.x},${to.y}`;
  const guess = isGuess(data) && !data.highlight;
  const width = strokeWidth(data.count) + (data.highlight ? 1.6 : 0);
  const title = data.extra ? `${edgeTitle(data)} (highlighted path step)` : edgeTitle(data);
  const color = data.highlight ? HIGHLIGHT_COLOR[data.highlight] : edgeColor(data.kind);
  const marker = data.highlight ? `ra-arrow-hl-${data.highlight}` : markerId(data.kind);
  return (
    <>
      <path
        d={path}
        className="ra-edge react-flow__edge-path"
        data-s={data.sourceNo}
        data-t={data.targetNo}
        data-kind={data.kind}
        data-guess={guess || undefined}
        data-crossing={data.crossing || undefined}
        data-highlight={data.highlight}
        fill="none"
        markerEnd={`url(#${marker})`}
        style={{
          stroke: color,
          strokeWidth: width,
          strokeDasharray: guess ? GUESS_DASH : undefined,
          // Only when dimmed: otherwise CSS (dense views, focus mode) decides.
          opacity: data.dimmed ? 0.22 : undefined,
        }}
      />
      <path
        d={path}
        className="react-flow__edge-interaction cursor-pointer"
        fill="none"
        stroke="transparent"
        strokeWidth={14}
      >
        <title>{`${title}. Click to see each link.`}</title>
      </path>
      {(near || data.alwaysCount) && data.count > 1 && data.showCount ? (
        <EdgeLabelRenderer>
          <div
            className="ra-edge-count nodrag nopan"
            // Same tags as the line, so focus mode fades the count with its edge.
            data-s={data.sourceNo}
            data-t={data.targetNo}
            data-highlight={data.highlight}
            data-always={data.alwaysCount || undefined}
            data-crossing={data.crossing || undefined}
            style={{
              transform: `translate(-50%, -50%) translate(${(from.x + to.x) / 2}px, ${(from.y + to.y) / 2}px)`,
              borderColor: edgeColor(data.kind),
              opacity: data.dimmed ? 0.22 : undefined,
            }}
            title={title}
          >
            {data.count > 999 ? `${Math.round(data.count / 100) / 10}k` : data.count}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
});

/** Arrowheads for every edge kind, colored by the theme. Render once per page. */
export function EdgeMarkers() {
  return (
    <svg aria-hidden="true" className="pointer-events-none absolute h-0 w-0">
      <defs>
        {EDGE_KINDS.map((kind) => (
          <marker
            key={kind}
            id={markerId(kind)}
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="9"
            markerHeight="9"
            markerUnits="userSpaceOnUse"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 9 5 L 0 9 z" fill={edgeColor(kind)} />
          </marker>
        ))}
        {HIGHLIGHT_STYLES.map((style) => (
          <marker
            key={style}
            id={`ra-arrow-hl-${style}`}
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="11"
            markerHeight="11"
            markerUnits="userSpaceOnUse"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 9 5 L 0 9 z" fill={HIGHLIGHT_COLOR[style]} />
          </marker>
        ))}
      </defs>
    </svg>
  );
}

export const EDGE_TYPES = { map: MapEdge };
