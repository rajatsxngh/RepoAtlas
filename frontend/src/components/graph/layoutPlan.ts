/**
 * The layout work the visible graph needs: one entry per container (the root and every expanded
 * node), with its direct children and the edges between them. A container's edges include edges
 * between its children's descendants, lifted to the children.
 */

import type { LayoutEdge } from "@/workers/layoutEngine";
import { nodeLabel } from "./flow";
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
  return { id: entry.id, label, nested: entry.expanded, leaf: leafSize(label) };
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
  const chain = (id: string): string[] => {
    const out = [id];
    for (let at = parentOf.get(id); at !== undefined; at = parentOf.get(at)) out.push(at);
    return out;
  };
  for (const edge of edges) {
    // Both chains end at the root, so they always meet; the edge belongs to the container where
    // they meet, between the two children on the way down. An edge between a container and
    // something inside it is not a layout constraint.
    const up = chain(edge.source);
    const down = chain(edge.target);
    const downSet = new Set(down);
    const meet = up.findIndex((id) => downSet.has(id));
    if (meet <= 0) continue;
    const lca = up[meet]!;
    const at = down.indexOf(lca);
    if (at <= 0) continue;
    const sourceChild = up[meet - 1]!;
    const targetChild = down[at - 1]!;
    const container = containers.get(lca);
    if (!container || sourceChild === targetChild) continue;
    container.edges.push({ source: sourceChild, target: targetChild });
  }
  const planned = [...containers.values()];
  for (const container of planned) {
    const edgeIds = [...new Set(container.edges.map((e) => `${e.source}>${e.target}`))].sort();
    const children = container.children.map((c) => c.id).join("\n");
    container.signature = `${children}\n--\n${edgeIds.join("\n")}`;
  }
  return planned.sort((a, b) => b.depth - a.depth);
}
