"use client";

/**
 * The links behind one line on the map: for an aggregated edge between two boxes, each import,
 * call, or test link it counts, with where it happens. Every name is repo text, rendered as text.
 */

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, X } from "lucide-react";
import type { SelectedEdge } from "@/components/graph/GraphCanvas";
import { EDGE_LABELS, LOW_CONFIDENCE } from "@/components/graph/edgeStyle";
import { KindIcon } from "@/components/graph/KindIcon";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { ApiNode, Edge } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface EdgePanelProps {
  snapshotId: number;
  edge: SelectedEdge;
  /** Open a node (one end of a link). */
  onNavigate?: (nodeKey: string) => void;
  onClose?: () => void;
}

export const EDGE_LINK_LIMIT = 50;

function shortName(key: string): string {
  if (key === "dir:.") return "/";
  const colon = key.indexOf(":");
  return colon >= 0 ? key.slice(colon + 1) : key;
}

/** "a/b.py:42", where a link happens: its site, else the source's file. */
export function linkPlace(link: Edge, nodes: ReadonlyMap<string, ApiNode>): string {
  // A test link's evidence is in the test (destination) file.
  const at = nodes.get(link.kind === "tested_by" ? link.dst : link.src);
  const path = at?.path ?? "";
  return link.site ? `${path}:${link.site.start_line}` : path;
}

function NodeButton({
  node,
  fallback,
  onNavigate,
}: {
  node: ApiNode | undefined;
  fallback: string;
  onNavigate?: (key: string) => void;
}) {
  const label = node ? (node.kind === "file" ? node.path : node.qualified_name) : fallback;
  return (
    <button
      type="button"
      className="flex min-w-0 cursor-pointer items-center gap-1.5 rounded px-1 text-left font-mono text-[12px] text-fg hover:bg-surface-2"
      title={label}
      onClick={() => node && onNavigate?.(node.key)}
    >
      {node ? <KindIcon kind={node.kind} className="h-3.5 w-3.5 shrink-0" /> : null}
      <span className="truncate">{label}</span>
    </button>
  );
}

export function EdgePanel({ snapshotId, edge, onNavigate, onClose }: EdgePanelProps) {
  const api = useApi();
  const found = useQuery({
    queryKey: queryKeys.edgeLinks(snapshotId, edge.src, edge.dst, edge.kind),
    queryFn: ({ signal }) =>
      api.getEdgeLinks(
        snapshotId,
        { from: edge.src, to: edge.dst, kind: edge.kind, limit: EDGE_LINK_LIMIT },
        signal,
      ),
  });
  const label = EDGE_LABELS[edge.kind];
  const nodes = new Map((found.data?.nodes ?? []).map((node) => [node.key, node]));
  const total = found.data?.total ?? edge.count;

  return (
    <aside
      aria-label="Link details"
      className="flex h-full w-full flex-col border-l border-line bg-surface"
    >
      <header className="flex items-start gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium tracking-wide text-fg-faint uppercase">
            {total.toLocaleString("en-US")} {total === 1 ? label.one : label.many}
          </p>
          <h2 className="flex min-w-0 items-center gap-1.5 text-base font-semibold text-fg">
            <span className="truncate" title={edge.src}>
              {shortName(edge.src)}
            </span>
            <ArrowRight className="h-4 w-4 shrink-0 text-fg-faint" aria-hidden="true" />
            <span className="truncate" title={edge.dst}>
              {shortName(edge.dst)}
            </span>
          </h2>
        </div>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close link details"
            onClick={onClose}
            icon={<X className="h-4 w-4" aria-hidden="true" />}
          />
        ) : null}
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {found.isPending ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
            <Spinner /> Loading links…
          </p>
        ) : found.isError ? (
          <div role="alert" className="text-sm">
            <p className="font-medium text-fg">{describeError(found.error).title}</p>
            <p className="mt-0.5 text-fg-muted">{describeError(found.error).detail}</p>
          </div>
        ) : (
          <>
            <ul className="space-y-2" aria-label="Links">
              {found.data.links.map((link, index) => (
                <li
                  key={`${link.src}|${link.dst}|${index}`}
                  className="rounded-lg border border-line px-2 py-1.5"
                >
                  <div className="flex min-w-0 items-center gap-1">
                    <NodeButton
                      node={nodes.get(link.src)}
                      fallback={link.src}
                      onNavigate={onNavigate}
                    />
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-fg-faint" aria-hidden="true" />
                    <NodeButton
                      node={nodes.get(link.dst)}
                      fallback={link.dst}
                      onNavigate={onNavigate}
                    />
                  </div>
                  <p className="mt-0.5 flex gap-2 px-1 text-[11px] text-fg-faint">
                    <span className="truncate font-mono">{linkPlace(link, nodes)}</span>
                    {link.confidence < LOW_CONFIDENCE ? (
                      <span className="shrink-0 text-warn">guessed by name</span>
                    ) : null}
                  </p>
                </li>
              ))}
            </ul>
            {found.data.total > found.data.links.length ? (
              <p className="mt-3 text-[12px] text-fg-muted">
                Showing the strongest {found.data.links.length} of{" "}
                {found.data.total.toLocaleString("en-US")}. Open the boxes on the map to see the
                rest by file.
              </p>
            ) : null}
          </>
        )}
      </div>
    </aside>
  );
}
