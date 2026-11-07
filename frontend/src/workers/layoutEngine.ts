/**
 * Lays out containers with elkjs (layered, top to bottom). Each container is laid out on its own,
 * with its children as boxes of known size; containers in one request are processed children
 * first, so a container's size is known before its parent is laid out.
 */

import type { ELK, ElkNode, LayoutOptions } from "elkjs/lib/elk-api";
import { containerSize, type Size } from "@/components/graph/sizes";

export interface LayoutChild {
  id: string;
  width: number;
  height: number;
  /** This child is itself laid out earlier in the same request; its size comes from that. */
  nested?: boolean;
}

export interface LayoutEdge {
  source: string;
  target: string;
  /** How many relationships the edge stands for (default 1); the strongest shape the layout. */
  weight?: number;
}

export interface ContainerInput {
  id: string;
  /** Header label of the container, which sets its minimum width (unused for the root). */
  label: string;
  children: LayoutChild[];
  edges: LayoutEdge[];
}

export interface ContainerLayout {
  id: string;
  /** Child positions relative to the container's content origin. */
  positions: Record<string, { x: number; y: number }>;
  /** Sizes the children had when they were placed. */
  sizes: Record<string, Size>;
  /** Extent of the content. */
  content: Size;
}

export const LAYOUT_OPTIONS: LayoutOptions = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.edgeRouting": "POLYLINE",
  "elk.spacing.nodeNode": "22",
  "elk.layered.spacing.nodeNodeBetweenLayers": "56",
  // The map draws edges straight between node borders, not along ELK's routes, so the room ELK
  // keeps around routed edges is kept small.
  "elk.layered.spacing.edgeNodeBetweenLayers": "6",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "4",
  "elk.spacing.edgeEdge": "4",
  "elk.spacing.edgeNode": "6",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF", // replaced per container by placementFor()
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.crossingMinimization.forceNodeModelOrder": "false",
  "elk.separateConnectedComponents": "true",
  "elk.spacing.componentComponent": "36",
  "elk.aspectRatio": "1.8", // replaced per container by aspectRatioFor()
  "elk.layered.compaction.connectedComponents": "true",
  "elk.padding": "[top=0,left=0,bottom=0,right=0]",
};

/**
 * ELK packs disconnected nodes into rows whose width follows the aspect ratio and the total
 * area; for a few small nodes that means one per row. Few children get a wider ratio, so a
 * handful of folders sit side by side instead of in a column.
 */
export function aspectRatioFor(childCount: number): number {
  return (
    Math.round(Math.min(8, Math.max(1.8, 16 / Math.sqrt(Math.max(1, childCount)))) * 100) / 100
  );
}

/**
 * Each child keeps its strongest few outgoing and incoming edges as layout constraints (the map
 * still draws every edge). Dense containers otherwise fill with long edges that ELK threads
 * through every layer in between, which spreads the layout out and slows it down.
 */
export const SKELETON_EDGES = 3;

/**
 * Network simplex places nodes about twice as compactly as Brandes-Koepf on dense containers, but
 * its cost climbs steeply with size (a dense 120-node container took about 30 s, against about
 * 1 s). Containers up to this many children get the compact placement.
 */
export const COMPACT_PLACEMENT_MAX_CHILDREN = 40;

export function placementFor(childCount: number): "NETWORK_SIMPLEX" | "BRANDES_KOEPF" {
  return childCount <= COMPACT_PLACEMENT_MAX_CHILDREN ? "NETWORK_SIMPLEX" : "BRANDES_KOEPF";
}

interface WeightedEdge {
  source: string;
  target: string;
  weight: number;
}

function byWeightThenId(other: (edge: WeightedEdge) => string) {
  return (a: WeightedEdge, b: WeightedEdge) => {
    if (a.weight !== b.weight) return b.weight - a.weight;
    const x = other(a);
    const y = other(b);
    return x < y ? -1 : x > y ? 1 : 0;
  };
}

/**
 * The edges to lay out: duplicates merged (weights added), self-loops and edges to unknown nodes
 * dropped, then each node's `perNode` strongest outgoing and incoming edges (ties broken by the
 * other end's id, so the result is deterministic), in their original order.
 */
export function skeletonEdges(
  edges: readonly LayoutEdge[],
  ids: ReadonlySet<string>,
  perNode: number = SKELETON_EDGES,
): WeightedEdge[] {
  const merged = new Map<string, WeightedEdge>();
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target) continue;
    const id = `${edge.source}\u0000${edge.target}`;
    const existing = merged.get(id);
    if (existing) existing.weight += edge.weight ?? 1;
    else merged.set(id, { source: edge.source, target: edge.target, weight: edge.weight ?? 1 });
  }
  const all = [...merged.values()];
  const outgoing = new Map<string, WeightedEdge[]>();
  const incoming = new Map<string, WeightedEdge[]>();
  for (const edge of all) {
    const out = outgoing.get(edge.source);
    if (out) out.push(edge);
    else outgoing.set(edge.source, [edge]);
    const into = incoming.get(edge.target);
    if (into) into.push(edge);
    else incoming.set(edge.target, [edge]);
  }
  const keep = new Set<WeightedEdge>();
  for (const list of outgoing.values()) {
    for (const edge of list.sort(byWeightThenId((e) => e.target)).slice(0, perNode)) keep.add(edge);
  }
  for (const list of incoming.values()) {
    for (const edge of list.sort(byWeightThenId((e) => e.source)).slice(0, perNode)) keep.add(edge);
  }
  return all.filter((edge) => keep.has(edge));
}

/** Lays out the containers in order (children before parents). */
export async function layoutContainers(
  elk: ELK,
  containers: readonly ContainerInput[],
): Promise<ContainerLayout[]> {
  const results: ContainerLayout[] = [];
  const sizes = new Map<string, Size>();
  for (const container of containers) {
    const childSizes: Record<string, Size> = {};
    const children: ElkNode[] = container.children.map((child) => {
      const size = (child.nested && sizes.get(child.id)) || {
        width: child.width,
        height: child.height,
      };
      childSizes[child.id] = size;
      return { id: child.id, width: size.width, height: size.height };
    });
    const ids = new Set(container.children.map((child) => child.id));
    const edges = skeletonEdges(container.edges, ids).map((edge, index) => ({
      id: `e${index}`,
      sources: [edge.source],
      targets: [edge.target],
    }));
    const positions: Record<string, { x: number; y: number }> = {};
    let content: Size = { width: 0, height: 0 };
    if (children.length) {
      const layoutOptions = {
        ...LAYOUT_OPTIONS,
        "elk.aspectRatio": String(aspectRatioFor(children.length)),
        "elk.layered.nodePlacement.strategy": placementFor(children.length),
      };
      const laid = await elk.layout({ id: "root", layoutOptions, children, edges });
      let right = 0;
      let bottom = 0;
      for (const child of laid.children ?? []) {
        const x = Math.round(child.x ?? 0);
        const y = Math.round(child.y ?? 0);
        positions[child.id] = { x, y };
        right = Math.max(right, x + (child.width ?? 0));
        bottom = Math.max(bottom, y + (child.height ?? 0));
      }
      content = { width: Math.ceil(right), height: Math.ceil(bottom) };
    }
    sizes.set(container.id, containerSize(content, container.label));
    results.push({ id: container.id, positions, sizes: childSizes, content });
  }
  return results;
}
