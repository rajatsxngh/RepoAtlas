/** Turns the visible graph and its layout into React Flow nodes and edges. */

import { Position, type Edge as FlowEdge, type Node as FlowNode } from "@xyflow/react";
import type { ApiNode, EdgeKind } from "@/lib/api/types";
import { bundle } from "./edgeStyle";
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
};

export type ContainerData = {
  node: ApiNode;
  label: string;
  meta: string;
  shown: number;
  total: number;
  loading: boolean;
  pulse: boolean;
};

export type MoreData = { container: string; hidden: number };

export type EdgeData = {
  kind: EdgeKind;
  count: number;
  minConfidence: number;
  maxConfidence: number;
  /** Perpendicular offset that keeps parallel edges of one pair apart. */
  offset: number;
  /** This edge carries its pair's count label. */
  showCount: boolean;
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
  childCount: (key: string) => number | undefined;
  fileCount: (key: string) => number | undefined;
}

export interface FlowOutput {
  nodes: MapFlowNode[];
  edges: MapFlowEdge[];
  /** Box size of every rendered node, by id. */
  sizes: Map<string, Size>;
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
        data: { container: entry.container, hidden: entry.hidden.length },
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
        },
      });
    }
  }
  const drawn = input.edges.filter(
    (edge) => rendered.has(edge.source) && rendered.has(edge.target),
  );
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
    },
  }));
  return { nodes, edges, sizes };
}
