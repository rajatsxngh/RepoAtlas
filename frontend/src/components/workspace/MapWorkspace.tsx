"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { GraphCanvas, type GraphCanvasHandle } from "@/components/graph/GraphCanvas";
import type { NodeAction } from "@/components/graph/layers";
import { NodePanel } from "@/components/panel/NodePanel";
import { SiteHeader } from "@/components/ui/SiteHeader";
import { NODE_KINDS, type SnapshotView } from "@/lib/api/types";

export interface MapWorkspaceProps {
  snapshot: SnapshotView;
}

/** Actions every node offers in its panel and context menu. */
export const WORKSPACE_ACTIONS: NodeAction[] = [
  { id: "focus", label: "Center on the map", appliesTo: [...NODE_KINDS] },
];

/** The map page: header with the repository, the map, and the selected node's details. */
export function MapWorkspace({ snapshot }: MapWorkspaceProps) {
  const repo = `${snapshot.repo.owner}/${snapshot.repo.name}`;
  const canvas = useRef<GraphCanvasHandle>(null);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    document.title = `${repo} · RepoAtlas`;
  }, [repo]);

  const show = useCallback(async (key: string) => {
    if (await canvas.current?.revealNode(key)) canvas.current?.focusNode(key);
  }, []);

  const onAction = useCallback(
    (key: string, actionId: string) => {
      if (actionId === "focus") void show(key);
    },
    [show],
  );

  return (
    <div className="flex h-screen flex-col">
      <SiteHeader>
        <span className="truncate font-mono text-sm text-fg">{repo}</span>
        <span className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg-muted">
          {snapshot.commit_sha.slice(0, 7)}
        </span>
      </SiteHeader>
      <div className="flex min-h-0 flex-1 border-t border-line">
        <main className="relative min-w-0 flex-1">
          <GraphCanvas
            ref={canvas}
            snapshotId={snapshot.id}
            layers={[]}
            selectedKey={selected}
            onSelect={setSelected}
            nodeActions={WORKSPACE_ACTIONS}
            onNodeAction={onAction}
          />
        </main>
        {selected ? (
          <div className="w-[min(420px,40vw)] shrink-0">
            <NodePanel
              key={selected}
              snapshotId={snapshot.id}
              nodeKey={selected}
              actions={WORKSPACE_ACTIONS}
              onAction={onAction}
              onNavigate={(key) => {
                setSelected(key);
                void show(key);
              }}
              onClose={() => setSelected(null)}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
