/**
 * Mock mode's impact analysis: the API's reverse traversal (`repoatlas.impact.traversal`) over the
 * fixture graph, without the parts that need the store's text search. From the target, impact
 * spreads backwards along `calls`, `inherits`, `imports`, and `routes_to` and forwards along
 * `tested_by`; a step scores `parent × decay × confidence`; several parents combine with noisy-or;
 * nodes below `min_score` or `max_depth` steps away are not expanded. Unknown sites come from
 * nothing here, so the list is empty.
 */

import type {
  ApiNode,
  Edge,
  EdgeKind,
  EvidenceRef,
  ImpactGroup,
  ImpactItem,
  ImpactParams,
  ImpactResponse,
  PathStep,
} from "../types";
import type { MockGraph } from "./graph";
import { MockApiError } from "./graph";

export const DEFAULT_IMPACT_PARAMS: ImpactParams = {
  max_depth: 4,
  decay: 0.6,
  min_score: 0.05,
  limit: 200,
  edge_kinds: ["calls", "inherits", "imports", "tested_by", "routes_to"],
};

interface Reach {
  best: number;
  depth: number;
  via: { src: string; edge: Edge } | null;
  parents: Map<string, number>;
  expanded: boolean;
}

function groupOf(node: ApiNode): ImpactGroup {
  if (node.kind === "test" || (node.kind === "file" && node.meta.is_test === true)) return "test";
  if (node.kind === "route" || node.meta.public === true) return "public_api";
  return "caller";
}

function score(reach: Reach): number {
  let remaining = 1;
  for (const value of reach.parents.values()) remaining *= 1 - value;
  return 1 - remaining;
}

function steps(graph: MockGraph, key: string, kinds: readonly EdgeKind[]) {
  const found: { next: string; edge: Edge }[] = [];
  for (const kind of kinds) {
    if (kind === "tested_by") {
      for (const edge of graph.edgesFrom(key)) {
        if (edge.kind === kind) found.push({ next: edge.dst, edge });
      }
    } else {
      for (const edge of graph.edgesInto(key)) {
        if (edge.kind === kind) found.push({ next: edge.src, edge });
      }
    }
  }
  return found;
}

export function mockImpact(
  graph: MockGraph,
  symbolKey: string,
  runId: number,
  params: ImpactParams = DEFAULT_IMPACT_PARAMS,
): ImpactResponse {
  if (!graph.nodes.has(symbolKey)) {
    throw new MockApiError(
      404,
      "not_found",
      `This snapshot has no node ${JSON.stringify(symbolKey)}.`,
      {
        resource: "node",
      },
    );
  }
  const started = performance.now();
  const reached = new Map<string, Reach>([
    [symbolKey, { best: 1, depth: 0, via: null, parents: new Map(), expanded: false }],
  ]);
  let expanded = 0;
  for (;;) {
    let current: string | null = null;
    for (const [key, reach] of reached) {
      if (reach.expanded) continue;
      const other = current === null ? null : reached.get(current)!;
      if (
        other === null ||
        reach.best > other.best ||
        (reach.best === other.best && (reach.depth < other.depth || key < current!))
      ) {
        current = key;
      }
    }
    if (current === null) break;
    const reach = reached.get(current)!;
    reach.expanded = true;
    const isStart = current === symbolKey;
    if (!isStart && (reach.best < params.min_score || reach.depth >= params.max_depth)) continue;
    if (params.max_depth === 0) continue;
    expanded += 1;
    for (const { next, edge } of steps(graph, current, params.edge_kinds)) {
      if (next === symbolKey || next === current) continue;
      const contribution = reach.best * params.decay * edge.confidence;
      let other = reached.get(next);
      if (!other) {
        other = { best: 0, depth: reach.depth + 1, via: null, parents: new Map(), expanded: false };
        reached.set(next, other);
      }
      if (contribution > (other.parents.get(current) ?? 0))
        other.parents.set(current, contribution);
      if (!other.expanded && contribution > other.best) {
        other.best = contribution;
        other.depth = reach.depth + 1;
        other.via = { src: current, edge };
      }
    }
  }

  const pathTo = (key: string): PathStep[] => {
    const path: PathStep[] = [];
    let at = reached.get(key)!;
    let here = key;
    while (at.via) {
      path.unshift({
        src: at.via.src,
        dst: here,
        kind: at.via.edge.kind as PathStep["kind"],
        confidence: at.via.edge.confidence,
      });
      here = at.via.src;
      at = reached.get(here)!;
    }
    return path;
  };

  const evidence = (node: ApiNode, edge: Edge | undefined): EvidenceRef[] => {
    const where = edge?.site ?? node.name_range ?? node.span;
    if (!where) return [];
    try {
      const slice = graph.code(node.path, where.start_line, where.end_line);
      return [
        {
          blob_oid: slice.blob_oid,
          path: node.path,
          start_line: where.start_line,
          end_line: Math.max(where.end_line, where.start_line),
          symbol_key: node.key,
        },
      ];
    } catch {
      return [];
    }
  };

  const all = [...reached.entries()]
    .filter(([key]) => key !== symbolKey)
    .map(([key, reach]) => ({ key, reach, score: score(reach) }))
    .filter((entry) => entry.score >= params.min_score)
    .sort(
      (a, b) =>
        b.score - a.score ||
        b.reach.best - a.reach.best ||
        a.reach.depth - b.reach.depth ||
        (a.key < b.key ? -1 : a.key > b.key ? 1 : 0),
    );
  const kept = all.slice(0, params.limit);
  const items: ImpactItem[] = kept.map(({ key, reach, score: value }) => {
    const node = graph.nodes.get(key)!;
    return {
      symbol_key: key,
      kind: node.kind,
      name: node.name,
      path: node.path,
      group: groupOf(node),
      score: Math.round(value * 1e6) / 1e6,
      best_score: Math.round(reach.best * 1e6) / 1e6,
      depth: reach.depth,
      best_path: pathTo(key),
      evidence: evidence(node, reach.via?.edge),
    };
  });
  const byGroup: Record<string, number> = { test: 0, public_api: 0, caller: 0 };
  for (const item of items) byGroup[item.group] = (byGroup[item.group] ?? 0) + 1;
  return {
    target: { kind: "symbol", symbol_key: symbolKey },
    targets: [symbolKey],
    params,
    items,
    unknown: [],
    stats: {
      targets: 1,
      reached: reached.size - 1,
      expanded,
      items: all.length,
      returned: items.length,
      truncated: all.length > kept.length,
      by_group: byGroup,
      unknown: 0,
      elapsed_ms: Math.round((performance.now() - started) * 1000) / 1000,
    },
    run_id: runId,
    trace_id: `00000000-0000-4000-8000-${String(runId).padStart(12, "0")}`,
  };
}
