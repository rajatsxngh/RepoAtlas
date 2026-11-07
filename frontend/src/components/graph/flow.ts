/** Turns the visible graph and its layout into React Flow nodes and edges. */

import { Position, type Edge as FlowEdge, type Node as FlowNode } from "@xyflow/react";
import type { ApiNode, EdgeKind } from "@/lib/api/types";
import { bundle } from "./edgeStyle";
import type { HighlightStyle, NodeHighlight, ResolvedLayers } from "./layers";
import { ROOT, mayHaveChildren, type VisibleEdge, type VisibleNode } from "./model";
import { CONTENT_OFFSET, MORE_SIZE, containerSize, leafSize, type Size } from "./sizes";

export type EntityData = {
  node: ApiNode;
  label: string;
  meta: string;
  expandable: boolean;
  loading: boolean;
  dimmed: boolean;
  /** Briefly ringed after a search or reveal brings it into view. */
  pulse: boolean;
  highlight?: NodeHighlight;
};

export type ContainerData = {
  node: ApiNode;
  label: string;
  meta: string;
  shown: number;
  total: number;
  loading: boolean;
  pulse: boolean;
  highlight?: NodeHighlight;
};

export type MoreData = { container: string; hidden: number; highlight?: NodeHighlight };

export type EdgeData = {
  kind: EdgeKind;
  count: number;
  minConfidence: number;
  maxConfidence: number;
  /** Perpendicular offset that keeps parallel edges of one pair apart. */
  offset: number;
  /** This edge carries its pair's count label. */
  showCount: boolean;
  /** Numbers of the two ends (see `createNumbering`), for focus mode's CSS. */
  sourceNo?: number;
  targetNo?: number;
  /** Drawn in a highlight layer's color, on top. */
  highlight?: HighlightStyle;
  /** Faded while a layer highlights other edges. */
  dimmed?: boolean;
  /** Not a graph edge of this view: a layer edge with nothing else to land on. */
  extra?: boolean;
};

export type EntityFlowNode = FlowNode<EntityData, "entity">;
export type ContainerFlowNode = FlowNode<ContainerData, "container">;
export type MoreFlowNode = FlowNode<MoreData, "more">;
export type MapFlowNode = EntityFlowNode | ContainerFlowNode | MoreFlowNode;
export type MapFlowEdge = FlowEdge<EdgeData, "map">;

export function nodeLabel(node: ApiNode): string {
  if (node.kind === "dir") return node.name === "." ? "/" : node.name;
  return node.name;
}

function plural(count: number, word: string): string {
  return `${count.toLocaleString("en-US")} ${word}${count === 1 ? "" : "s"}`;
}

export function nodeMeta(node: ApiNode, fileCount?: number): string {
  switch (node.kind) {
    case "dir":
      return fileCount === undefined ? "folder" : plural(fileCount, "file");
    case "file":
      return typeof node.meta.loc === "number" ? plural(node.meta.loc, "line") : "file";
    case "route":
      return node.span ? `route · line ${node.span.start_line}` : "route";
    default:
      return node.span
        ? node.span.start_line === node.span.end_line
          ? `line ${node.span.start_line}`
          : `lines ${node.span.start_line}–${node.span.end_line}`
        : node.kind;
  }
}

/** Where a container's children sit, and how much room they take. */
export interface PlacedContainer {
  positions: Readonly<Record<string, { x: number; y: number }>>;
  content: Size;
}

export interface FlowInput {
  visible: readonly VisibleNode[];
  edges: readonly VisibleEdge[];
  layouts: ReadonlyMap<string, PlacedContainer>;
  /** Containers whose children are being fetched or laid out. */
  loading: ReadonlySet<string>;
  selected: string | null;
  /** A node to ring briefly (after focusNode). */
  pulse?: string | null;
  /** Where the highlight layers land. */
  layers?: ResolvedLayers;
  /** Most edges to draw (default `EDGE_BUDGET`). */
  edgeBudget?: number;
  /** Edge kinds the reader switched off (highlighted edges still show). */
  hiddenKinds?: ReadonlySet<EdgeKind>;
  /** A stable number per node key (see `createNumbering`). */
  numberOf: (key: string) => number;
  childCount: (key: string) => number | undefined;
  fileCount: (key: string) => number | undefined;
}

export interface FlowOutput {
  nodes: MapFlowNode[];
  edges: MapFlowEdge[];
  /** Box size of every rendered node, by id. */
  sizes: Map<string, Size>;
  /** Edges between drawn nodes, and how many of them are drawn (the edge budget). */
  edgeCount: { total: number; drawn: number };
}

/**
 * Gives each node key a number that never changes, so focus mode's CSS can name nodes without
 * repo text and an edge's data stays equal across renders (an edge that didn't change keeps its
 * object, and React Flow skips it).
 */
export function createNumbering(): (key: string) => number {
  const numbers = new Map<string, number>();
  return (key) => {
    let number = numbers.get(key);
    if (number === undefined) {
      number = numbers.size;
      numbers.set(key, number);
    }
    return number;
  };
}

/**
 * Most edges drawn at once. Like the node cap, it keeps big maps readable and fast: thousands
 * of overlapping lines carry no information and make every update slow.
 */
export const EDGE_BUDGET = 1200;

const KIND_ORDER: Record<EdgeKind, number> = {
  calls: 0,
  imports: 1,
  inherits: 2,
  routes_to: 3,
  tested_by: 4,
  contains: 5,
};

/**
 * The edges to draw when there are more than the budget: highlighted ones, then those touching
 * the selected node, then the heaviest (most underlying edges), then by kind and id.
 */
export function withinBudget(
  edges: readonly VisibleEdge[],
  budget: number,
  keep: (edge: VisibleEdge) => number,
): VisibleEdge[] {
  if (edges.length <= budget) return [...edges];
  return [...edges]
    .sort(
      (a, b) =>
        keep(b) - keep(a) ||
        b.count - a.count ||
        KIND_ORDER[a.kind] - KIND_ORDER[b.kind] ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    )
    .slice(0, budget);
}

/** The box a visible node takes: a leaf, a laid-out container, or the "more" pill. */
export function boxSize(entry: VisibleNode, layouts: ReadonlyMap<string, PlacedContainer>): Size {
  if (entry.type === "more") return MORE_SIZE;
  const label = nodeLabel(entry.node);
  const layout = entry.expanded ? layouts.get(entry.id) : undefined;
  return layout ? containerSize(layout.content, label) : leafSize(label, nodeMeta(entry.node));
}

export function toFlow(input: FlowInput): FlowOutput {
  const nodes: MapFlowNode[] = [];
  const sizes = new Map<string, Size>();
  const rendered = new Set<string>();
  const hiddenIn = new Map<string, number>();
  for (const entry of input.visible) {
    if (entry.type === "more") hiddenIn.set(entry.container, entry.hidden.length);
  }
  for (const entry of input.visible) {
    const parentId = entry.parent;
    if (parentId !== null && !rendered.has(parentId)) continue;
    const layout = input.layouts.get(parentId ?? ROOT);
    const position = layout?.positions[entry.id];
    if (!position) continue;
    const size = boxSize(entry, input.layouts);
    const offset = parentId === null ? { x: 0, y: 0 } : CONTENT_OFFSET;
    const common = {
      id: entry.id,
      position: { x: position.x + offset.x, y: position.y + offset.y },
      width: size.width,
      height: size.height,
      // The map sets every box's size itself, so it also declares the size and the anchors.
      // Without declared handles React Flow waits for DOM measurements, which it drops whenever
      // a node object is replaced, and edges vanish.
      measured: { width: size.width, height: size.height },
      handles: [
        {
          type: "target" as const,
          position: Position.Top,
          x: size.width / 2,
          y: 0,
          width: 1,
          height: 1,
        },
        {
          type: "source" as const,
          position: Position.Bottom,
          x: size.width / 2,
          y: size.height,
          width: 1,
          height: 1,
        },
      ],
      style: { width: size.width, height: size.height },
      ...(parentId === null ? {} : { parentId }),
      draggable: false,
      connectable: false,
    };
    rendered.add(entry.id);
    sizes.set(entry.id, size);
    if (entry.type === "more") {
      nodes.push({
        ...common,
        type: "more",
        selectable: false,
        data: {
          container: entry.container,
          hidden: entry.hidden.length,
          highlight: input.layers?.nodes.get(entry.id),
        },
      });
      continue;
    }
    const label = nodeLabel(entry.node);
    const meta = nodeMeta(entry.node, input.fileCount(entry.id));
    if (entry.expanded && input.layouts.has(entry.id)) {
      const total = input.childCount(entry.id) ?? 0;
      nodes.push({
        ...common,
        type: "container",
        selected: input.selected === entry.id,
        zIndex: entry.depth,
        data: {
          node: entry.node,
          label,
          meta,
          shown: total - (hiddenIn.get(entry.id) ?? 0),
          total,
          loading: input.loading.has(entry.id),
          pulse: input.pulse === entry.id,
          highlight: input.layers?.nodes.get(entry.id),
        },
      });
    } else {
      nodes.push({
        ...common,
        type: "entity",
        selected: input.selected === entry.id,
        zIndex: entry.depth + 1,
        data: {
          node: entry.node,
          label,
          meta,
          expandable: mayHaveChildren(entry.node),
          loading: input.loading.has(entry.id),
          dimmed: false,
          pulse: input.pulse === entry.id,
          highlight: input.layers?.nodes.get(entry.id),
        },
      });
    }
  }
  const highlighted = input.layers?.edges;
  const between = input.edges.filter(
    (edge) =>
      rendered.has(edge.source) &&
      rendered.has(edge.target) &&
      (!input.hiddenKinds?.has(edge.kind) || highlighted?.has(edge.id)),
  );
  const drawn = withinBudget(between, input.edgeBudget ?? EDGE_BUDGET, (edge) =>
    highlighted?.has(edge.id)
      ? 2
      : edge.source === input.selected || edge.target === input.selected
        ? 1
        : 0,
  );
  const dimOthers = Boolean(highlighted?.size || input.layers?.extra.length);
  const { offsets, labelled } = bundle(drawn);
  const edges: MapFlowEdge[] = drawn.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "map",
    selectable: false,
    focusable: false,
    data: {
      kind: edge.kind,
      count: edge.count,
      minConfidence: edge.minConfidence,
      maxConfidence: edge.maxConfidence,
      offset: offsets.get(edge.id) ?? 0,
      showCount: labelled.has(edge.id),
      highlight: highlighted?.get(edge.id)?.style,
      dimmed: dimOthers && !highlighted?.has(edge.id),
      sourceNo: input.numberOf(edge.source),
      targetNo: input.numberOf(edge.target),
    },
  }));
  for (const extra of input.layers?.extra ?? []) {
    if (!rendered.has(extra.source) || !rendered.has(extra.target)) continue;
    edges.push({
      id: extra.id,
      source: extra.source,
      target: extra.target,
      type: "map",
      selectable: false,
      focusable: false,
      data: {
        kind: extra.kind,
        count: 1,
        minConfidence: 1,
        maxConfidence: 1,
        offset: 0,
        showCount: false,
        highlight: extra.style,
        extra: true,
        sourceNo: input.numberOf(extra.source),
        targetNo: input.numberOf(extra.target),
      },
    });
  }
  // Edges sit under every node (manual z-index); highlighted ones go last so they draw on top
  // of the other edges.
  edges.sort((a, b) => Number(Boolean(a.data?.highlight)) - Number(Boolean(b.data?.highlight)));
  return {
    nodes,
    edges,
    sizes,
    edgeCount: { total: between.length, drawn: drawn.length },
  };
}
