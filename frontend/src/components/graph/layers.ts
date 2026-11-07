/**
 * Highlight layers: named sets of nodes and edges drawn over the map. Features add layers
 * instead of changing the canvas; several can be active at once.
 *
 * A layer entry on a node the map doesn't show bubbles up to its visible stand-in (the nearest
 * collapsed ancestor, or the "+N more" node hiding it), which shows the highest score and how
 * many hidden entries it stands for. Layer edges land on the visible edge between the two
 * stand-ins, or are drawn as an extra edge when the map has none there.
 */

import type { EdgeKind, NodeKind } from "@/lib/api/types";
import { parseEdgeKey } from "@/lib/graph/keys";

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

/** Which style wins when layers overlap on one node or edge. */
export const STYLE_PRIORITY: readonly HighlightStyle[] = [
  "warning",
  "primary",
  "secondary",
  "muted",
];

export interface NodeHighlight {
  /** The winning style, and every style present (most important first). */
  style: HighlightStyle;
  styles: HighlightStyle[];
  layers: string[];
  /** Highest score among the entries here (1 when no layer gave a score). */
  score: number;
  /** The node itself is in a layer (not only hidden things below it). */
  direct: boolean;
  /** Layer entries on hidden nodes this one stands for. */
  hidden: number;
}

export interface EdgeHighlight {
  style: HighlightStyle;
  layers: string[];
}

export interface ExtraEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  style: HighlightStyle;
  layers: string[];
}

export interface ResolvedLayers {
  nodes: Map<string, NodeHighlight>;
  edges: Map<string, EdgeHighlight>;
  /** Layer edges with no visible edge to land on, drawn on top. */
  extra: ExtraEdge[];
}

function byPriority(a: HighlightStyle, b: HighlightStyle): number {
  return STYLE_PRIORITY.indexOf(a) - STYLE_PRIORITY.indexOf(b);
}

function clampScore(value: number | undefined): number {
  if (value === undefined || Number.isNaN(value)) return 1;
  return Math.min(1, Math.max(0, value));
}

/**
 * Where every layer lands on the current map. `proxyOf` gives a key's visible stand-in (or
 * null); `visibleEdges` holds the ids (`"src|dst|kind"` of visible ids) of the drawn edges.
 */
export function resolveLayers(
  layers: readonly HighlightLayer[],
  proxyOf: (key: string) => string | null,
  visibleEdges: ReadonlySet<string>,
): ResolvedLayers {
  const nodes = new Map<string, NodeHighlight>();
  const edges = new Map<string, EdgeHighlight>();
  const extra = new Map<string, ExtraEdge>();
  const proxies = new Map<string, string | null>();
  const proxy = (key: string) => {
    if (!proxies.has(key)) proxies.set(key, proxyOf(key));
    return proxies.get(key)!;
  };

  for (const layer of layers) {
    const seen = new Set<string>();
    for (const key of layer.nodeKeys) {
      if (seen.has(key)) continue;
      seen.add(key);
      const target = proxy(key);
      if (!target) continue;
      const direct = target === key;
      const score = clampScore(layer.scores?.[key]);
      const entry = nodes.get(target);
      if (!entry) {
        nodes.set(target, {
          style: layer.style,
          styles: [layer.style],
          layers: [layer.id],
          score,
          direct,
          hidden: direct ? 0 : 1,
        });
        continue;
      }
      entry.score = Math.max(entry.score, score);
      entry.direct ||= direct;
      if (!direct) entry.hidden += 1;
      if (!entry.layers.includes(layer.id)) entry.layers.push(layer.id);
      if (!entry.styles.includes(layer.style)) {
        entry.styles.push(layer.style);
        entry.styles.sort(byPriority);
        entry.style = entry.styles[0]!;
      }
    }
    for (const value of layer.edgeKeys ?? []) {
      const parsed = parseEdgeKey(value);
      if (!parsed) continue;
      const source = proxy(parsed.src);
      const target = proxy(parsed.dst);
      if (!source || !target || source === target) continue;
      const forward = `${source}|${target}|${parsed.kind}`;
      const backward = `${target}|${source}|${parsed.kind}`;
      const id = visibleEdges.has(forward) ? forward : visibleEdges.has(backward) ? backward : null;
      if (id) {
        const entry = edges.get(id);
        if (!entry) edges.set(id, { style: layer.style, layers: [layer.id] });
        else {
          if (!entry.layers.includes(layer.id)) entry.layers.push(layer.id);
          if (byPriority(layer.style, entry.style) < 0) entry.style = layer.style;
        }
        continue;
      }
      const extraId = `layer:${forward}`;
      const existing = extra.get(extraId);
      if (existing) {
        if (!existing.layers.includes(layer.id)) existing.layers.push(layer.id);
        if (byPriority(layer.style, existing.style) < 0) existing.style = layer.style;
      } else {
        extra.set(extraId, {
          id: extraId,
          source,
          target,
          kind: parsed.kind as EdgeKind,
          style: layer.style,
          layers: [layer.id],
        });
      }
    }
  }
  return { nodes, edges, extra: [...extra.values()] };
}
