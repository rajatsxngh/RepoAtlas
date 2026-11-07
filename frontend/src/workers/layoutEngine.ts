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
  "elk.spacing.nodeNode": "28",
  "elk.layered.spacing.nodeNodeBetweenLayers": "56",
  "elk.layered.spacing.edgeNodeBetweenLayers": "20",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
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
    const seen = new Set<string>();
    const edges = container.edges
      .filter((edge) => ids.has(edge.source) && ids.has(edge.target) && edge.source !== edge.target)
      .filter((edge) => {
        const id = `${edge.source}\u0000${edge.target}`;
        if (seen.has(id)) return false;
        seen.add(id);
        return true;
      })
      .map((edge, index) => ({ id: `e${index}`, sources: [edge.source], targets: [edge.target] }));
    const positions: Record<string, { x: number; y: number }> = {};
    let content: Size = { width: 0, height: 0 };
    if (children.length) {
      const layoutOptions = {
        ...LAYOUT_OPTIONS,
        "elk.aspectRatio": String(aspectRatioFor(children.length)),
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
