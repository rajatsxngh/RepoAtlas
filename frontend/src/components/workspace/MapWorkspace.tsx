"use client";

import { useEffect } from "react";
import { SiteHeader } from "@/components/ui/SiteHeader";
import type { SnapshotView } from "@/lib/api/types";

export interface MapWorkspaceProps {
  snapshot: SnapshotView;
}

/** The map page: header with the repository, and the map below it. */
export function MapWorkspace({ snapshot }: MapWorkspaceProps) {
  const repo = `${snapshot.repo.owner}/${snapshot.repo.name}`;
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
      <main className="relative flex-1 border-t border-line" aria-label="Code map" />
    </div>
  );
}
