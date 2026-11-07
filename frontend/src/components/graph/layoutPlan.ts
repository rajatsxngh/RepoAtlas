/**
 * The layout work the visible graph needs: one entry per container (the root and every expanded
 * node), with its direct children and the edges between them. A container's edges include edges
 * between its children's descendants, lifted to the children.
 */

import type { LayoutEdge } from "@/workers/layoutEngine";
import { nodeLabel, nodeMeta } from "./flow";
import { ROOT, type VisibleEdge, type VisibleNode } from "./model";
import { MORE_SIZE, leafSize, type Size } from "./sizes";

export interface PlannedChild {
  id: string;
  label: string;
  /** The child is an expanded container; its size comes from its own layout. */
  nested: boolean;
  /** Its box while collapsed (or while its own layout is pending). */
  leaf: Size;
}

export interface PlannedContainer {
  id: string;
  label: string;
  children: PlannedChild[];
  edges: LayoutEdge[];
  /** Changes when the children or their edges change (then ELK runs again). */
  signature: string;
  depth: number;
}

function plannedChild(entry: VisibleNode): PlannedChild {
  if (entry.type === "more") return { id: entry.id, label: "", nested: false, leaf: MORE_SIZE };
  const label = nodeLabel(entry.node);
  return {
    id: entry.id,
    label,
    nested: entry.expanded,
    leaf: leafSize(label, nodeMeta(entry.node)),
  };
}

/** Containers in children-first order, each with what ELK needs. */
export function planLayout(
  visible: readonly VisibleNode[],
  edges: readonly VisibleEdge[],
): PlannedContainer[] {
  const parentOf = new Map<string, string>();
  const depthOf = new Map<string, number>([[ROOT, 0]]);
  const containers = new Map<string, PlannedContainer>();
  containers.set(ROOT, { id: ROOT, label: "", children: [], edges: [], signature: "", depth: 0 });
  for (const entry of visible) {
    const parent = entry.parent ?? ROOT;
    parentOf.set(entry.id, parent);
    depthOf.set(entry.id, (depthOf.get(parent) ?? 0) + 1);
    if (entry.type === "node" && entry.expanded) {
      containers.set(entry.id, {
        id: entry.id,
        label: nodeLabel(entry.node),
        children: [],
        edges: [],
        signature: "",
        depth: depthOf.get(entry.id)!,
      });
    }
  }
  for (const entry of visible) {
    containers.get(entry.parent ?? ROOT)?.children.push(plannedChild(entry));
  }
  const depth = (id: string) => depthOf.get(id) ?? 0;
  const parent = (id: string) => parentOf.get(id) ?? ROOT;
  for (const edge of edges) {
    // Climb from both ends to the container where they meet; the edge belongs to that container,
    // between the two children on the way down. An edge between a container and something
    // inside it is not a layout constraint. (Depth-based climbing: no allocation per edge.)
    let a = edge.source;
    let b = edge.target;
    if (!depthOf.has(a) || !depthOf.has(b)) continue;
    let belowA: string | null = null;
    let belowB: string | null = null;
    while (depth(a) > depth(b)) {
      belowA = a;
      a = parent(a);
    }
    while (depth(b) > depth(a)) {
      belowB = b;
      b = parent(b);
    }
    while (a !== b) {
      belowA = a;
      belowB = b;
      a = parent(a);
      b = parent(b);
    }
    if (belowA === null || belowB === null || belowA === belowB) continue;
    containers.get(a)?.edges.push({ source: belowA, target: belowB, weight: edge.count });
  }
  const planned = [...containers.values()];
  for (const container of planned) {
    // Weights pick the edges that shape the layout, so they belong in the signature too.
    const weights = new Map<string, number>();
    for (const e of container.edges) {
      const id = `${e.source}>${e.target}`;
      weights.set(id, (weights.get(id) ?? 0) + (e.weight ?? 1));
    }
    const edgeIds = [...weights].map(([id, weight]) => `${id}:${weight}`).sort();
    const children = container.children.map((c) => c.id).join("\n");
    container.signature = `${children}\n--\n${edgeIds.join("\n")}`;
  }
  return planned.sort((a, b) => b.depth - a.depth);
}
