"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Route } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GraphCanvas, type GraphCanvasHandle } from "@/components/graph/GraphCanvas";
import type { NodeAction } from "@/components/graph/layers";
import { NodePanel } from "@/components/panel/NodePanel";
import { PathFinder, pathLayer } from "@/components/path/PathFinder";
import { SearchBox } from "@/components/search/SearchBox";
import { Button } from "@/components/ui/Button";
import { SiteHeader } from "@/components/ui/SiteHeader";
import { useApi } from "@/lib/api/context";
import { NODE_KINDS, type ApiNode, type GraphPath, type SnapshotView } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface MapWorkspaceProps {
  snapshot: SnapshotView;
}

const PATH_KINDS = NODE_KINDS.filter((kind) => kind !== "dir");

/** Actions every node offers in its panel and context menu. */
export const WORKSPACE_ACTIONS: NodeAction[] = [
  { id: "focus", label: "Center on the map", appliesTo: [...NODE_KINDS] },
  { id: "path-from", label: "Find a path from here", appliesTo: PATH_KINDS },
  { id: "path-to", label: "Find a path to here", appliesTo: PATH_KINDS },
];

/** The map page: header with the repository and search, the map, and the selected node. */
export function MapWorkspace({ snapshot }: MapWorkspaceProps) {
  const api = useApi();
  const queryClient = useQueryClient();
  const repo = `${snapshot.repo.owner}/${snapshot.repo.name}`;
  const canvas = useRef<GraphCanvasHandle>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pathOpen, setPathOpen] = useState(false);
  const [from, setFrom] = useState<ApiNode | null>(null);
  const [to, setTo] = useState<ApiNode | null>(null);
  const [path, setPath] = useState<GraphPath | null>(null);
  const layers = useMemo(() => (path ? [pathLayer(path)] : []), [path]);

  useEffect(() => {
    document.title = `${repo} · RepoAtlas`;
  }, [repo]);

  const show = useCallback(async (key: string) => {
    if (await canvas.current?.revealNode(key)) canvas.current?.focusNode(key);
  }, []);

  const pick = useCallback(
    (key: string) => {
      setSelected(key);
      void show(key);
    },
    [show],
  );

  const nodeOf = useCallback(
    async (key: string) =>
      (
        await queryClient.fetchQuery({
          queryKey: queryKeys.node(snapshot.id, key),
          queryFn: ({ signal }) => api.getNode(snapshot.id, key, signal),
        })
      ).node,
    [api, queryClient, snapshot.id],
  );

  const onAction = useCallback(
    (key: string, actionId: string) => {
      if (actionId === "focus") void show(key);
      if (actionId === "path-from" || actionId === "path-to") {
        setPathOpen(true);
        setPath(null);
        void nodeOf(key).then((node) => (actionId === "path-from" ? setFrom(node) : setTo(node)));
      }
    },
    [nodeOf, show],
  );

  const onFound = useCallback(async (found: GraphPath) => {
    setPath(found);
    const keys = found.nodes.map((node) => node.key);
    await canvas.current?.revealNodes(keys);
    canvas.current?.fitView(keys);
  }, []);

  const clearPath = useCallback(() => {
    setPath(null);
    setFrom(null);
    setTo(null);
  }, []);

  return (
    <div className="flex h-screen flex-col">
      <SiteHeader>
        <span className="hidden truncate font-mono text-sm text-fg sm:inline">{repo}</span>
        <span className="hidden rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg-muted sm:inline">
          {snapshot.commit_sha.slice(0, 7)}
        </span>
        <div className="ml-auto flex w-full max-w-lg items-center gap-2">
          <SearchBox snapshotId={snapshot.id} onPick={(node) => pick(node.key)} slashShortcut />
          <Button
            variant={pathOpen ? "primary" : "secondary"}
            aria-pressed={pathOpen}
            onClick={() => setPathOpen((open) => !open)}
            icon={<Route className="h-4 w-4" aria-hidden="true" />}
          >
            Path
          </Button>
        </div>
      </SiteHeader>
      <div className="flex min-h-0 flex-1 border-t border-line">
        <main className="relative min-w-0 flex-1">
          <GraphCanvas
            ref={canvas}
            snapshotId={snapshot.id}
            layers={layers}
            selectedKey={selected}
            onSelect={setSelected}
            nodeActions={WORKSPACE_ACTIONS}
            onNodeAction={onAction}
          />
          {pathOpen ? (
            <div className="absolute top-3 left-3 z-10">
              <PathFinder
                snapshotId={snapshot.id}
                from={from}
                to={to}
                onChangeFrom={(node) => {
                  setPath(null);
                  setFrom(node);
                }}
                onChangeTo={(node) => {
                  setPath(null);
                  setTo(node);
                }}
                path={path}
                onFound={(found) => void onFound(found)}
                onClear={clearPath}
                onStep={pick}
                onClose={() => setPathOpen(false)}
              />
            </div>
          ) : null}
        </main>
        {selected ? (
          <div className="w-[min(420px,40vw)] shrink-0">
            <NodePanel
              key={selected}
              snapshotId={snapshot.id}
              nodeKey={selected}
              actions={WORKSPACE_ACTIONS}
              onAction={onAction}
              onNavigate={pick}
              onClose={() => setSelected(null)}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
