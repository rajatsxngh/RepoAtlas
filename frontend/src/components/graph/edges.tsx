"use client";

import { BaseEdge, useInternalNode, type EdgeProps, type InternalNode } from "@xyflow/react";
import { memo } from "react";
import { EDGE_KINDS, type EdgeKind } from "@/lib/api/types";
import type { MapFlowEdge } from "./flow";
import { floatingEnds, type Rect } from "./geometry";

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

export const MapEdge = memo(function MapEdge({ id, source, target, data }: EdgeProps<MapFlowEdge>) {
  const from = useInternalNode(source);
  const to = useInternalNode(target);
  if (!from || !to || !data) return null;
  const ends = floatingEnds(rectOf(from), rectOf(to));
  const path = `M ${ends.from.x},${ends.from.y} L ${ends.to.x},${ends.to.y}`;
  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={`url(#${markerId(data.kind)})`}
      style={{ stroke: edgeColor(data.kind), strokeWidth: 1.5 }}
    />
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
