"use client";

/**
 * Finds a shortest path between two symbols (`/path`, over calls and imports) and shows its steps.
 * The workspace draws the result as a highlight layer and reveals every node on it.
 */

import { useMutation } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Route, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { KindIcon } from "@/components/graph/KindIcon";
import type { HighlightLayer } from "@/components/graph/layers";
import { EDGE_LABELS } from "@/components/graph/edgeStyle";
import { SearchBox } from "@/components/search/SearchBox";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError, isApiError } from "@/lib/api/errors";
import type { ApiNode, GraphPath } from "@/lib/api/types";
import { edgeKey } from "@/lib/graph/keys";

export const PATH_LAYER_ID = "path";

/** The path as a primary highlight layer: its nodes and its edges. */
export function pathLayer(path: GraphPath): HighlightLayer {
  const first = path.nodes[0];
  const last = path.nodes.at(-1);
  return {
    id: PATH_LAYER_ID,
    nodeKeys: path.nodes.map((node) => node.key),
    edgeKeys: path.edges.map((edge) => edgeKey(edge.src, edge.dst, edge.kind)),
    style: "primary",
    label: first && last ? `Path: ${first.name} → ${last.name}` : "Path",
  };
}

export interface PathFinderProps {
  snapshotId: number;
  from: ApiNode | null;
  to: ApiNode | null;
  onChangeFrom: (node: ApiNode | null) => void;
  onChangeTo: (node: ApiNode | null) => void;
  path: GraphPath | null;
  onFound: (path: GraphPath) => void;
  onClear: () => void;
  /** A step was clicked. */
  onStep?: (key: string) => void;
  onClose?: () => void;
}

function Endpoint({
  label,
  node,
  snapshotId,
  onChange,
}: {
  label: string;
  node: ApiNode | null;
  snapshotId: number;
  onChange: (node: ApiNode | null) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-9 shrink-0 text-[12px] text-fg-muted">{label}</span>
      {node ? (
        <span className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5">
          <KindIcon kind={node.kind} className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate text-[13px] text-fg" title={node.qualified_name}>
            {node.name}
          </span>
          <button
            type="button"
            className="flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded text-fg-faint hover:bg-surface-3 hover:text-fg"
            aria-label={`Clear the ${label.toLowerCase()} symbol`}
            onClick={() => onChange(null)}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </span>
      ) : (
        <SearchBox
          snapshotId={snapshotId}
          onPick={onChange}
          label={`Path ${label.toLowerCase()}`}
          placeholder="Search a symbol…"
          kinds={["file", "class", "function", "method", "route", "test"]}
        />
      )}
    </div>
  );
}

export function PathFinder({
  snapshotId,
  from,
  to,
  onChangeFrom,
  onChangeTo,
  path,
  onFound,
  onClear,
  onStep,
  onClose,
}: PathFinderProps) {
  const api = useApi();
  const find = useMutation({
    mutationFn: ({ source, target }: { source: string; target: string }) =>
      api.findPath(snapshotId, { from: source, to: target }),
    onSuccess: onFound,
  });
  const lastPair = useRef<string | null>(null);

  // Run as soon as both ends are set (also when they come from node actions).
  useEffect(() => {
    if (!from || !to) {
      lastPair.current = null;
      return;
    }
    const pair = `${from.key}\u0000${to.key}`;
    if (lastPair.current === pair) return;
    lastPair.current = pair;
    find.mutate({ source: from.key, target: to.key });
  }, [from, to, find]);

  const notFound = isApiError(find.error) && find.error.status === 404;

  return (
    <section
      aria-label="Path finder"
      className="w-[min(360px,calc(100vw-2rem))] rounded-xl border border-line bg-surface/95 p-3 text-fg shadow-pop backdrop-blur"
    >
      <header className="mb-2.5 flex items-center gap-2">
        <Route className="h-4 w-4 text-hl-primary" aria-hidden="true" />
        <h2 className="flex-1 text-[13px] font-semibold">Find a path</h2>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            aria-label="Close the path finder"
            onClick={onClose}
            icon={<X className="h-4 w-4" aria-hidden="true" />}
          />
        ) : null}
      </header>
      <div className="space-y-2">
        <Endpoint label="From" node={from} snapshotId={snapshotId} onChange={onChangeFrom} />
        <Endpoint label="To" node={to} snapshotId={snapshotId} onChange={onChangeTo} />
      </div>
      <p className="mt-2 text-[11px] text-fg-faint">
        Follows calls and imports. Right-click a node for “Find a path from here”.
      </p>

      {find.isPending ? (
        <p className="mt-3 flex items-center gap-2 text-[13px] text-fg-muted" role="status">
          <Spinner className="h-3.5 w-3.5" /> Looking for a path…
        </p>
      ) : find.isError ? (
        <p className="mt-3 text-[13px] text-fg-muted" role="alert">
          {notFound && from && to
            ? `No path connects ${from.name} and ${to.name} through calls or imports.`
            : describeError(find.error).detail}
        </p>
      ) : path ? (
        <div className="mt-3">
          <div className="flex items-baseline justify-between">
            <p className="text-[12px] font-medium text-fg">
              {path.nodes.length} {path.nodes.length === 1 ? "step" : "steps"}
            </p>
            <Button size="sm" variant="ghost" onClick={onClear}>
              Clear
            </Button>
          </div>
          {!path.directed ? (
            <p className="mt-1 text-[11px] text-fg-muted">
              No path follows the edge directions; this one goes against some of them.
            </p>
          ) : null}
          <ol className="mt-2 max-h-72 overflow-y-auto" aria-label="Path steps">
            {path.nodes.map((node, index) => {
              const edge = path.edges[index];
              const forward = edge ? edge.src === node.key : true;
              return (
                <li key={`${node.key}-${index}`}>
                  <button
                    type="button"
                    className="flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-left text-[12px] hover:bg-surface-2"
                    onClick={() => onStep?.(node.key)}
                    title={`${node.qualified_name} — ${node.path}`}
                  >
                    <span className="w-4 shrink-0 text-right text-fg-faint tabular-nums">
                      {index + 1}
                    </span>
                    <KindIcon kind={node.kind} className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate text-fg">{node.name}</span>
                  </button>
                  {edge ? (
                    <p className="flex items-center gap-1 py-0.5 pl-8 text-[11px] text-fg-faint">
                      {forward ? (
                        <ArrowDown className="h-3 w-3" aria-hidden="true" />
                      ) : (
                        <ArrowUp className="h-3 w-3" aria-hidden="true" />
                      )}
                      {forward
                        ? EDGE_LABELS[edge.kind].many
                        : `${EDGE_LABELS[edge.kind].many} (against the direction)`}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
