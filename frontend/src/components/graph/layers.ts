/**
 * Highlight layers: named sets of nodes and edges drawn over the map. Features add layers
 * instead of changing the canvas; several can be active at once.
 */

import type { NodeKind } from "@/lib/api/types";

export type HighlightStyle = "primary" | "secondary" | "warning" | "muted";

export type HighlightLayer = {
  id: string;
  nodeKeys: string[];
  /** `"src|dst|kind"` (see `edgeKey` in `@/lib/graph/keys`). */
  edgeKeys?: string[];
  style: HighlightStyle;
  /** 0..1 per node key, shown as intensity. */
  scores?: Record<string, number>;
  /** Shown in the legend. */
  label?: string;
};

/** An action offered on nodes of some kinds, in the node panel and the context menu. */
export type NodeAction = {
  id: string;
  label: string;
  appliesTo: NodeKind[];
};
