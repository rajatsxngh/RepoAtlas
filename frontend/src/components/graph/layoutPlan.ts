/**
 * The layout work the visible graph needs: one entry per container (the root and every expanded
 * node), with its direct children and the edges between them. A container's edges include edges
 * between its children's descendants, lifted to the children.
 */

import type { ContainerInput, LayoutEdge } from "@/workers/layoutEngine";
import { boxSize, nodeLabel } from "./flow";
import { ROOT, type VisibleEdge, type VisibleNode } from "./model";

export interface PlannedContainer extends ContainerInput {
  /** Changes when the children or their edges change (then ELK runs again). */
  signature: string;
  depth: number;
}

/** Containers in children-first order, each with what ELK needs. */
export function planLayout(
  visible: readonly VisibleNode[],
  edges: readonly VisibleEdge[],
  sizeOf: (entry: VisibleNode) => { width: number; height: number },
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
    const container = containers.get(entry.parent ?? ROOT);
    if (!container) continue;
    const nested = entry.type === "node" && entry.expanded;
    const size = sizeOf(entry);
    container.children.push({ id: entry.id, width: size.width, height: size.height, nested });
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
    const edgeIds = [
      ...new Set(container.edges.map((e: LayoutEdge) => `${e.source}>${e.target}`)),
    ].sort();
    container.signature = `${container.children.map((c) => c.id).join("\n")}\n--\n${edgeIds.join("\n")}`;
  }
  return planned.sort((a, b) => b.depth - a.depth);
}

export { boxSize };
