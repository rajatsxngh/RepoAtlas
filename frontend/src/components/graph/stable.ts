/**
 * Keeps React Flow nodes and edges that didn't change as the same objects between renders, so
 * React Flow keeps their internals and memoized components skip re-rendering. On a big map an
 * expansion then re-renders the handful of things it touched, not thousands of edges.
 */

import type { FlowOutput, MapFlowEdge, MapFlowNode } from "./flow";
import type { NodeHighlight } from "./layers";

function sameHighlight(a: NodeHighlight | undefined, b: NodeHighlight | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.style === b.style &&
    a.score === b.score &&
    a.direct === b.direct &&
    a.hidden === b.hidden &&
    a.styles.join() === b.styles.join() &&
    a.layers.join() === b.layers.join()
  );
}

function sameData(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (key === "highlight") {
      if (
        !sameHighlight(a[key] as NodeHighlight | undefined, b[key] as NodeHighlight | undefined)
      ) {
        return false;
      }
    } else if (a[key] !== b[key]) {
      return false;
    }
  }
  return true;
}

export function sameNode(a: MapFlowNode, b: MapFlowNode): boolean {
  return (
    a.type === b.type &&
    a.position.x === b.position.x &&
    a.position.y === b.position.y &&
    a.width === b.width &&
    a.height === b.height &&
    a.parentId === b.parentId &&
    a.selected === b.selected &&
    a.zIndex === b.zIndex &&
    sameData(a.data as Record<string, unknown>, b.data as Record<string, unknown>)
  );
}

export function sameEdge(a: MapFlowEdge, b: MapFlowEdge): boolean {
  return (
    a.source === b.source &&
    a.target === b.target &&
    a.zIndex === b.zIndex &&
    sameData((a.data ?? {}) as Record<string, unknown>, (b.data ?? {}) as Record<string, unknown>)
  );
}

/** A function that returns the flow with unchanged items replaced by their previous objects. */
export function createStabilizer(): (output: FlowOutput) => FlowOutput {
  let nodes = new Map<string, MapFlowNode>();
  let edges = new Map<string, MapFlowEdge>();
  return (output) => {
    const nextNodes = output.nodes.map((node) => {
      const previous = nodes.get(node.id);
      return previous && sameNode(previous, node) ? previous : node;
    });
    const nextEdges = output.edges.map((edge) => {
      const previous = edges.get(edge.id);
      return previous && sameEdge(previous, edge) ? previous : edge;
    });
    nodes = new Map(nextNodes.map((node) => [node.id, node]));
    edges = new Map(nextEdges.map((edge) => [edge.id, edge]));
    return { ...output, nodes: nextNodes, edges: nextEdges };
  };
}
