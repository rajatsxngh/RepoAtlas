"use client";

import "@xyflow/react/dist/style.css";
import { useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type NodeMouseHandler,
} from "@xyflow/react";
import { ChevronsDownUp, LayoutGrid } from "lucide-react";
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type Ref,
} from "react";
import { Legend } from "@/components/legend/Legend";
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
import { GraphModel, ROOT, childLevel, mayHaveChildren } from "./model";
import { NodeMenu } from "./NodeMenu";
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

/** Minimap colors: kind accents for nodes, a faint fill for containers. */
function minimapColor(node: { type?: string; data: unknown }): string {
  if (node.type === "more") return "var(--ra-line-strong)";
  const kind = (node.data as { node?: { kind?: string } }).node?.kind ?? "file";
  if (node.type === "container") return "color-mix(in srgb, var(--ra-surface-3) 60%, transparent)";
  return `var(--ra-kind-${kind})`;
}

function Canvas({
  snapshotId,
  layers,
  onSelect,
  onNodeAction,
  nodeActions = [],
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
  const [fetching, setFetching] = useState<ReadonlySet<string>>(() => new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const fitted = useRef(false);
  const zoomBand = useStore((state) =>
    state.transform[2] < 0.55 ? "ra-far" : state.transform[2] < 0.85 ? "ra-mid" : "ra-near",
  );
  const filesReady = useRef<Promise<void>>(Promise.resolve());
  const revealAfterLayout = useRef<string | null>(null);
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
    filesReady.current = loadSubgraph(ROOT, "file")
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
        loading: fetching.size ? new Set([...layout.pending, ...fetching]) : layout.pending,
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
    [visible, edges, layouts, layout.pending, fetching, selectedKey, model],
  );

  useEffect(() => {
    if (fitted.current || graph.nodes.length === 0) return;
    fitted.current = true;
    const frame = requestAnimationFrame(
      () => void flow.fitView({ padding: 0.12, duration: 0, maxZoom: 1.1 }),
    );
    return () => cancelAnimationFrame(frame);
  }, [graph.nodes.length, flow]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(timer);
  }, [notice]);

  const labelOf = useCallback(
    (key: string) => {
      const node = model.nodes.get(key);
      return node
        ? node.kind === "dir" || node.kind === "file"
          ? node.path
          : node.qualified_name
        : key;
    },
    [model],
  );

  const reportClosed = useCallback(
    (closed: readonly string[]) => {
      if (!closed.length) return;
      const first = labelOf(closed[0]!);
      const rest = closed.length > 1 ? ` and ${closed.length - 1} more` : "";
      setNotice(`Closed ${first}${rest} to stay under ${model.cap} nodes.`);
    },
    [labelOf, model],
  );

  /** Loads a container's children if needed (and, for folders, the file-level edges). */
  const load = useCallback(
    async (key: string): Promise<boolean> => {
      const node = model.nodes.get(key);
      if (!node) return false;
      if (model.isLoaded(key)) return true;
      setFetching((current) => new Set(current).add(key));
      try {
        const [subgraph] = await Promise.all([
          loadSubgraph(key, childLevel(node)),
          filesReady.current,
        ]);
        model.addSubgraph(subgraph);
        return true;
      } catch (error) {
        setNotice(`Couldn't open ${labelOf(key)}: ${describeError(error).detail}`);
        return false;
      } finally {
        setFetching((current) => {
          const next = new Set(current);
          next.delete(key);
          return next;
        });
      }
    },
    [labelOf, loadSubgraph, model],
  );

  const expand = useCallback(
    async (key: string): Promise<boolean> => {
      if (!(await load(key))) return false;
      const outcome = model.expand(key, { makeRoom: true });
      sync();
      if (!outcome.ok) {
        if (outcome.reason === "empty") setNotice(`${labelOf(key)} has nothing inside to show.`);
        else if (outcome.reason === "cap")
          setNotice(`The map shows ${model.cap} nodes at most. Close a container to open more.`);
        return false;
      }
      reportClosed(outcome.closed);
      revealAfterLayout.current = key;
      return true;
    },
    [labelOf, load, model, reportClosed, sync],
  );

  const collapse = useCallback(
    (key: string) => {
      model.collapse(key);
      sync();
    },
    [model, sync],
  );

  const actions = useMemo<CanvasActions>(
    () => ({
      select: (key) => onSelect(key),
      toggle: (key) => {
        if (model.expanded.has(key)) collapse(key);
        else void expand(key);
      },
      showMore: (container) => {
        const shown = model.showMore(container);
        sync();
        if (!shown)
          setNotice(`The map shows ${model.cap} nodes at most. Close a container to see more.`);
      },
      openMenu: (key, at) => setMenu({ key, ...at }),
    }),
    [collapse, expand, model, onSelect, sync, setMenu],
  );

  const menuNode = menu ? model.nodes.get(menu.key) : undefined;
  const menuItems = menuNode
    ? [
        { id: "ra:details", label: "Show details" },
        ...(mayHaveChildren(menuNode)
          ? [{ id: "ra:toggle", label: model.expanded.has(menuNode.key) ? "Close" : "Open" }]
          : []),
        ...nodeActions
          .filter((action) => action.appliesTo.includes(menuNode.kind))
          .map((action) => ({ id: action.id, label: action.label })),
      ]
    : [];
  const pickMenu = (id: string) => {
    if (!menuNode) return;
    if (id === "ra:details") onSelect(menuNode.key);
    else if (id === "ra:toggle") actions.toggle(menuNode.key);
    else onNodeAction(menuNode.key, id);
  };

  // After a container opens and is laid out, bring it into view if it spills out of the screen.
  useEffect(() => {
    const key = revealAfterLayout.current;
    if (!key || layout.pending.size || !graph.sizes.has(key)) return;
    revealAfterLayout.current = null;
    const node = flow.getInternalNode(key);
    if (!node) return;
    const { x, y } = node.internals.positionAbsolute;
    const width = node.measured.width ?? 0;
    const height = node.measured.height ?? 0;
    const view = flow.getViewport();
    const box = document.querySelector(".react-flow")?.getBoundingClientRect();
    if (!box) return;
    const left = (x * view.zoom + view.x) | 0;
    const top = (y * view.zoom + view.y) | 0;
    const inside =
      left >= 0 &&
      top >= 0 &&
      left + width * view.zoom <= box.width &&
      top + height * view.zoom <= box.height;
    if (!inside) {
      void flow.fitView({
        nodes: [{ id: key }],
        padding: 0.15,
        duration: 350,
        maxZoom: Math.max(view.zoom, 0.6),
      });
    }
  }, [graph, layout.pending, flow]);

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
      expand,
      collapse,
    }),
    [collapse, expand, flow, model],
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
        className={zoomBand}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
        <Controls
          showInteractive={false}
          position="bottom-right"
          fitViewOptions={{ padding: 0.12, maxZoom: 1.1 }}
        >
          <ControlButton
            onClick={() => {
              model.collapseAll();
              sync();
            }}
            title="Close everything"
            aria-label="Close everything"
          >
            <ChevronsDownUp aria-hidden="true" />
          </ControlButton>
          <ControlButton
            onClick={layout.relayout}
            title="Tidy the layout"
            aria-label="Tidy the layout"
          >
            <LayoutGrid aria-hidden="true" />
          </ControlButton>
        </Controls>
        <MiniMap
          position="top-right"
          pannable
          zoomable
          nodeColor={minimapColor}
          nodeStrokeWidth={0}
          ariaLabel="Overview of the map"
        />
        <Panel position="bottom-left">
          <Legend layers={layers} />
        </Panel>
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
      {menu && menuNode ? (
        <NodeMenu
          title={`Actions for ${menuNode.name}`}
          items={menuItems}
          at={menu}
          onPick={pickMenu}
          onClose={() => setMenu(null)}
        />
      ) : null}
      <div
        className="pointer-events-none absolute inset-x-0 bottom-5 flex justify-center px-4"
        aria-live="polite"
      >
        {notice ? (
          <p className="pointer-events-auto max-w-lg rounded-full border border-line bg-surface px-4 py-2 text-sm text-fg shadow-pop">
            {notice}
          </p>
        ) : null}
      </div>
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
