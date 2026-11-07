/** How edges look: kind sets the color, confidence the dash, count the width and label. */

import type { EdgeKind } from "@/lib/api/types";

/** Below this confidence an edge is a guess (name-matched), drawn dashed (PRD MAP-5). */
export const LOW_CONFIDENCE = 0.6;
export const GUESS_DASH = "5 4";
/** Space between parallel edges of one pair of nodes. */
export const PARALLEL_SPACING = 5;

export const EDGE_LABELS: Record<EdgeKind, { one: string; many: string; legend: string }> = {
  contains: { one: "contains", many: "contains", legend: "Contains" },
  imports: { one: "import", many: "imports", legend: "Imports" },
  calls: { one: "call", many: "calls", legend: "Calls" },
  inherits: { one: "inheritance", many: "inheritances", legend: "Inherits from" },
  tested_by: { one: "test link", many: "test links", legend: "Tested by" },
  routes_to: { one: "route", many: "routes", legend: "Routes to" },
};

/** An aggregated edge is a guess only when every edge in it is. */
export function isGuess(edge: { maxConfidence: number }): boolean {
  return edge.maxConfidence < LOW_CONFIDENCE;
}

/** Wider for more edges, logarithmically, so a few heavy links don't drown the rest. */
export function strokeWidth(count: number): number {
  return Math.round(Math.min(4.5, 1.3 + Math.log2(Math.max(1, count)) * 0.6) * 100) / 100;
}

function formatConfidence(value: number): string {
  return value === 1 ? "1" : value.toFixed(2);
}

export function edgeTitle(edge: {
  kind: EdgeKind;
  count: number;
  minConfidence: number;
  maxConfidence: number;
}): string {
  const label = EDGE_LABELS[edge.kind];
  const what = `${edge.count.toLocaleString("en-US")} ${edge.count === 1 ? label.one : label.many}`;
  const range =
    edge.minConfidence === edge.maxConfidence
      ? formatConfidence(edge.minConfidence)
      : `${formatConfidence(edge.minConfidence)}–${formatConfidence(edge.maxConfidence)}`;
  const guess = isGuess(edge) ? " (guessed by name)" : "";
  return `${what}, confidence ${range}${guess}`;
}

/**
 * Offsets (in pixels, perpendicular to the line) for edges that share a pair of nodes, so edges
 * in both directions or of several kinds sit side by side instead of on top of each other.
 * Offsets are measured along the normal of the pair's canonical direction, so they don't depend
 * on which way an edge points.
 */
export function parallelOffsets(
  edges: readonly { id: string; source: string; target: string }[],
): Map<string, number> {
  return bundle(edges).offsets;
}

/**
 * Groups edges by their pair of nodes: the perpendicular offset of each, and the one edge per
 * pair that carries the count label (the heaviest; the rest show their counts on hover).
 */
export function bundle(
  edges: readonly { id: string; source: string; target: string; count?: number }[],
): { offsets: Map<string, number>; labelled: Set<string> } {
  const groups = new Map<string, { id: string; count: number }[]>();
  for (const edge of edges) {
    const pair =
      edge.source < edge.target
        ? `${edge.source}\u0000${edge.target}`
        : `${edge.target}\u0000${edge.source}`;
    const list = groups.get(pair) ?? [];
    list.push({ id: edge.id, count: edge.count ?? 1 });
    groups.set(pair, list);
  }
  const offsets = new Map<string, number>();
  const labelled = new Set<string>();
  for (const members of groups.values()) {
    members.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    members.forEach(({ id }, index) =>
      offsets.set(id, (index - (members.length - 1) / 2) * PARALLEL_SPACING),
    );
    const heaviest = members.reduce((best, member) => (member.count > best.count ? member : best));
    labelled.add(heaviest.id);
  }
  return { offsets, labelled };
}
