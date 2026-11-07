"use client";

import {
  EdgeLabelRenderer,
  useInternalNode,
  type EdgeProps,
  type InternalNode,
} from "@xyflow/react";
import { memo } from "react";
import { EDGE_KINDS, type EdgeKind } from "@/lib/api/types";
import { GUESS_DASH, edgeTitle, isGuess, strokeWidth } from "./edgeStyle";
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
  if (!sourceNode || !targetNode || !data) return null;
  const ends = floatingEnds(rectOf(sourceNode), rectOf(targetNode));
  const [from, to] = shift(ends.from, ends.to, data.offset, source > target);
  const path = `M ${from.x},${from.y} L ${to.x},${to.y}`;
  const guess = isGuess(data);
  const width = strokeWidth(data.count);
  const title = edgeTitle(data);
  return (
    <>
      <path
        d={path}
        className="ra-edge react-flow__edge-path"
        data-kind={data.kind}
        data-guess={guess || undefined}
        fill="none"
        markerEnd={`url(#${markerId(data.kind)})`}
        style={{
          stroke: edgeColor(data.kind),
          strokeWidth: width,
          strokeDasharray: guess ? GUESS_DASH : undefined,
        }}
      />
      <path
        d={path}
        className="react-flow__edge-interaction"
        fill="none"
        stroke="transparent"
        strokeWidth={14}
      >
        <title>{title}</title>
      </path>
      {data.count > 1 && data.showCount ? (
        <EdgeLabelRenderer>
          <div
            className="ra-edge-count nodrag nopan"
            style={{
              transform: `translate(-50%, -50%) translate(${(from.x + to.x) / 2}px, ${(from.y + to.y) / 2}px)`,
              borderColor: edgeColor(data.kind),
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
      </defs>
    </svg>
  );
}

export const EDGE_TYPES = { map: MapEdge };
