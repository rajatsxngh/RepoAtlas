"use client";

import { useEffect, useState } from "react";
import { GraphCanvas } from "@/components/graph/GraphCanvas";
import { SiteHeader } from "@/components/ui/SiteHeader";
import type { SnapshotView } from "@/lib/api/types";

export interface MapWorkspaceProps {
  snapshot: SnapshotView;
}

/** The map page: header with the repository, and the map below it. */
export function MapWorkspace({ snapshot }: MapWorkspaceProps) {
  const repo = `${snapshot.repo.owner}/${snapshot.repo.name}`;
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    document.title = `${repo} · RepoAtlas`;
  }, [repo]);
  return (
    <div className="flex h-screen flex-col">
      <SiteHeader>
        <span className="truncate font-mono text-sm text-fg">{repo}</span>
        <span className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg-muted">
          {snapshot.commit_sha.slice(0, 7)}
        </span>
      </SiteHeader>
      <main className="relative min-h-0 flex-1 border-t border-line">
        <GraphCanvas
          snapshotId={snapshot.id}
          layers={[]}
          selectedKey={selected}
          onSelect={setSelected}
          onNodeAction={() => {}}
        />
      </main>
    </div>
  );
}
