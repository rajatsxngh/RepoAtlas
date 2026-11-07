"use client";

import "@xyflow/react/dist/style.css";
import { useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type NodeMouseHandler,
} from "@xyflow/react";
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type Ref,
} from "react";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { SubgraphLevel } from "@/lib/api/types";
import { config } from "@/lib/config";
import { queryKeys } from "@/lib/query";
import { CanvasActionsContext, type CanvasActions } from "./actions";
import { EDGE_TYPES, EdgeMarkers } from "./edges";
import { toFlow } from "./flow";
import type { HighlightLayer, NodeAction } from "./layers";
import { getLayoutClient } from "./layoutClient";
import { planLayout } from "./layoutPlan";
import { GraphModel, ROOT } from "./model";
import { NODE_TYPES } from "./nodes";
import { useMapLayout } from "./useMapLayout";

export interface GraphCanvasHandle {
  /** Expands the node's ancestors in order so the node is visible. */
  revealNode(key: string): Promise<boolean>;
  /** Pans and zooms to the node. */
  focusNode(key: string): void;
  fitView(): void;
  expand(key: string): Promise<boolean>;
  collapse(key: string): void;
}

export interface GraphCanvasProps {
  snapshotId: number;
  layers: HighlightLayer[];
  onSelect: (nodeKey: string | null) => void;
  onNodeAction: (nodeKey: string, actionId: string) => void;
  nodeActions?: NodeAction[];
  /** The selected node, drawn with a focus ring. */
  selectedKey?: string | null;
  /** Most nodes visible at once (default: NEXT_PUBLIC_NODE_CAP or 300). */
  nodeCap?: number;
  ref?: Ref<GraphCanvasHandle>;
}

function Canvas({
  snapshotId,
  onSelect,
  selectedKey = null,
  nodeCap = config.nodeCap,
  ref,
}: GraphCanvasProps) {
  const api = useApi();
  const queryClient = useQueryClient();
  const flow = useReactFlow();
  const [model] = useState(() => new GraphModel(nodeCap));
  const [version, setVersion] = useState(0);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const fitted = useRef(false);
  const sync = useCallback(() => setVersion(model.version), [model]);

  const loadSubgraph = useCallback(
    (parent: string, level: SubgraphLevel) =>
      queryClient.fetchQuery({
        queryKey: queryKeys.subgraph(snapshotId, parent, level),
        queryFn: ({ signal }) => api.getSubgraph(snapshotId, { parent, level }, signal),
      }),
    [api, queryClient, snapshotId],
  );

  useEffect(() => {
    let alive = true;
    loadSubgraph(ROOT, "dir")
      .then((root) => {
        if (!alive) return;
        model.addSubgraph(root);
        sync();
      })
      .catch((error: unknown) => alive && setLoadError(error));
    loadSubgraph(ROOT, "file")
      .then((files) => {
        if (!alive) return;
        model.addFileAggregate(files);
        sync();
      })
      .catch(() => {
        // Without the file-level edges the map still works; edges stay on containers.
      });
    return () => {
      alive = false;
    };
  }, [attempt, loadSubgraph, model, sync]);

  const visible = useMemo(() => (version >= 0 ? model.visibleNodes() : []), [model, version]);
  const edges = useMemo(() => (version >= 0 ? model.visibleEdges() : []), [model, version]);
  const structure = useMemo(() => planLayout(visible, edges), [visible, edges]);
  const [client] = useState(() => (typeof window === "undefined" ? null : getLayoutClient()));
  const layout = useMapLayout(structure, client);
  const layouts = layout.placed;

  const graph = useMemo(
    () =>
      toFlow({
        visible,
        edges,
        layouts,
        loading: layout.pending,
        selected: selectedKey,
        childCount: (key) => model.childrenOf(key)?.length,
        fileCount: (key) => {
          const node = model.nodes.get(key);
          if (node?.kind !== "dir" || !model.hasFileEdges) return undefined;
          const prefix = node.path === "." ? "" : `${node.path}/`;
          let count = 0;
          for (const other of model.nodes.values())
            if (other.kind === "file" && other.path.startsWith(prefix)) count += 1;
          return count;
        },
      }),
    [visible, edges, layouts, layout.pending, selectedKey, model],
  );

  useEffect(() => {
    if (fitted.current || graph.nodes.length === 0) return;
    fitted.current = true;
    const frame = requestAnimationFrame(
      () => void flow.fitView({ padding: 0.12, duration: 0, maxZoom: 1.1 }),
    );
    return () => cancelAnimationFrame(frame);
  }, [graph.nodes.length, flow]);

  const actions = useMemo<CanvasActions>(
    () => ({
      select: (key) => onSelect(key),
      toggle: () => {},
      showMore: () => {},
      openMenu: () => {},
    }),
    [onSelect],
  );

  useImperativeHandle(
    ref,
    () => ({
      revealNode: async (key) => model.isVisible(key),
      focusNode: (key) => {
        const node = flow.getInternalNode(key);
        if (!node) return;
        const { x, y } = node.internals.positionAbsolute;
        const width = node.measured.width ?? 0;
        const height = node.measured.height ?? 0;
        void flow.setCenter(x + width / 2, y + height / 2, {
          zoom: Math.max(flow.getZoom(), 1),
          duration: 400,
        });
      },
      fitView: () => void flow.fitView({ padding: 0.12, duration: 300, maxZoom: 1.1 }),
      expand: async () => false,
      collapse: () => {},
    }),
    [flow, model],
  );

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      if (node.type !== "more") onSelect(node.id);
    },
    [onSelect],
  );

  if (loadError) {
    const { title, detail } = describeError(loadError);
    return (
      <div className="flex h-full items-center justify-center p-6" role="alert">
        <div className="max-w-sm rounded-2xl border border-line bg-surface p-5 shadow-card">
          <p className="font-semibold text-fg">{title}</p>
          <p className="mt-1 text-sm text-fg-muted">{detail}</p>
          <button
            type="button"
            className="mt-3 cursor-pointer text-sm font-medium text-focus underline underline-offset-2"
            onClick={() => {
              setLoadError(null);
              setAttempt((count) => count + 1);
            }}
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  const empty = model.isLoaded(ROOT) && (model.childrenOf(ROOT)?.length ?? 0) === 0;

  return (
    <CanvasActionsContext.Provider value={actions}>
      <span
        hidden
        data-testid="map-state"
        data-visible-nodes={visible.length}
        data-rendered-nodes={graph.nodes.length}
        data-rendered-edges={graph.edges.length}
        data-visible-edges={edges.length}
        data-layout-pending={layout.pending.size}
        data-layout-ms={layout.lastMs ?? ""}
      />
      <EdgeMarkers />
      <ReactFlow
        nodes={graph.nodes}
        edges={graph.edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodeClick={onNodeClick}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        minZoom={0.05}
        maxZoom={2.5}
        proOptions={{ hideAttribution: false }}
        aria-label="Code map"
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
      {!model.isLoaded(ROOT) ? (
        <div
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
          role="status"
        >
          <span className="flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-sm text-fg-muted shadow-card">
            <Spinner /> Loading the map…
          </span>
        </div>
      ) : null}
      {empty ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="rounded-xl border border-line bg-surface px-4 py-3 text-sm text-fg-muted shadow-card">
            This snapshot has no source files to map.
          </p>
        </div>
      ) : null}
    </CanvasActionsContext.Provider>
  );
}

/** The interactive map of one snapshot. */
export function GraphCanvas(props: GraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <div className="relative h-full w-full">
        <Canvas key={props.snapshotId} {...props} />
      </div>
    </ReactFlowProvider>
  );
}
