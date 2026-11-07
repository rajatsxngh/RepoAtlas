"use client";

/**
 * Details of the selected node: kind, symbol key, path and lines, owners, how it connects
 * (callers and callees, or importers and imports for files), the actions registered for its
 * kind, and its code. Every name shown is repo text, rendered as text.
 */

import { useQuery } from "@tanstack/react-query";
import { Check, Copy, X } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { CodeViewer } from "@/components/code/CodeViewer";
import { KIND_LABELS, KindIcon } from "@/components/graph/KindIcon";
import type { NodeAction } from "@/components/graph/layers";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { ApiNode, EdgeKind, Neighborhood, Owner } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface NodePanelProps {
  snapshotId: number;
  nodeKey: string;
  actions?: readonly NodeAction[];
  onAction?: (nodeKey: string, actionId: string) => void;
  /** Go to another node (a caller, a callee). */
  onNavigate?: (nodeKey: string) => void;
  onClose?: () => void;
}

const LIST_LIMIT = 8;

/** Text with line-break chances after "/", ":", and "." (as <wbr> elements, never markup). */
export function breakable(text: string): ReactNode {
  const parts = text.split(/(?<=[/:.])/);
  return parts.map((part, index) => (
    <Fragment key={index}>
      {part}
      {index < parts.length - 1 ? <wbr /> : null}
    </Fragment>
  ));
}

const DATE = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric" });

function relationFor(
  kind: ApiNode["kind"],
): { kind: EdgeKind; incoming: string; outgoing: string } | null {
  if (kind === "dir") return null;
  if (kind === "file") return { kind: "imports", incoming: "Imported by", outgoing: "Imports" };
  if (kind === "route")
    return { kind: "routes_to", incoming: "Reached from", outgoing: "Handled by" };
  return { kind: "calls", incoming: "Callers", outgoing: "Callees" };
}

function lineText(node: ApiNode): string | null {
  if (!node.span) return null;
  const { start_line: start, end_line: end } = node.span;
  return start === end ? `line ${start}` : `lines ${start}–${end}`;
}

function useNeighbors(
  snapshotId: number,
  nodeKey: string,
  direction: "in" | "out",
  kind: EdgeKind | null,
) {
  const api = useApi();
  return useQuery({
    enabled: kind !== null,
    queryKey: queryKeys.neighbors(snapshotId, nodeKey, direction, kind ? [kind] : [], 1),
    queryFn: ({ signal }) =>
      api.getNeighbors(
        snapshotId,
        nodeKey,
        { direction, kinds: kind ? [kind] : null, depth: 1 },
        signal,
      ),
  });
}

/**
 * Where a connection lives, shown after its name: a file's folder, or a symbol's file. Names come
 * first so that siblings with long shared paths stay tellable apart when the row is cut off.
 */
export function relationContext(node: Pick<ApiNode, "kind" | "path">): string {
  const slash = node.path.lastIndexOf("/");
  if (node.kind === "file" || node.kind === "dir")
    return slash > 0 ? node.path.slice(0, slash) : "";
  return slash >= 0 ? node.path.slice(slash + 1) : node.path;
}

function Relation({
  title,
  query,
  onNavigate,
}: {
  title: string;
  query: { data?: Neighborhood; isPending: boolean; isError: boolean };
  onNavigate?: (key: string) => void;
}) {
  const hops = query.data?.nodes ?? [];
  const sorted = [...hops].sort((a, b) => a.node.name.localeCompare(b.node.name));
  return (
    <div className="min-w-0">
      <h4 className="flex items-baseline justify-between text-[12px] font-medium text-fg-muted">
        {title}
        <span className="text-sm font-semibold text-fg tabular-nums">
          {query.isPending ? "…" : query.isError ? "?" : hops.length.toLocaleString("en-US")}
          {query.data?.truncated ? "+" : ""}
        </span>
      </h4>
      {sorted.length ? (
        <ul className="mt-1.5 space-y-0.5">
          {sorted.slice(0, LIST_LIMIT).map(({ node }) => {
            const where = relationContext(node);
            return (
              <li key={node.key}>
                <button
                  type="button"
                  className="flex w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-[12px] text-fg hover:bg-surface-2"
                  onClick={() => onNavigate?.(node.key)}
                  title={node.kind === "file" ? node.path : `${node.qualified_name} — ${node.path}`}
                >
                  <KindIcon kind={node.kind} className="h-3.5 w-3.5 shrink-0" />
                  <span className="min-w-0 truncate">
                    {node.name}
                    {where ? (
                      <>
                        {" "}
                        <span className="ml-0.5 text-fg-faint">{where}</span>
                      </>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
          {sorted.length > LIST_LIMIT ? (
            <li className="px-1.5 text-[11px] text-fg-faint">
              and {sorted.length - LIST_LIMIT} more
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

function Owners({ owners }: { owners: readonly Owner[] }) {
  if (!owners.length) {
    return <p className="text-[12px] text-fg-faint">No commit history for this file.</p>;
  }
  return (
    <ul className="space-y-2">
      {owners.slice(0, 3).map((owner) => (
        <li key={owner.author_hash} className="text-[12px]">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate font-medium text-fg">{owner.author_name}</span>
            <span className="shrink-0 text-fg-muted tabular-nums">
              {Math.round(owner.share * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
            <div
              className="h-full rounded-full bg-focus/70"
              style={{ width: `${Math.max(2, owner.share * 100)}%` }}
            />
          </div>
          <p className="mt-0.5 text-[11px] text-fg-faint">
            {owner.commits.toLocaleString("en-US")} {owner.commits === 1 ? "commit" : "commits"},
            last on {DATE.format(new Date(owner.last_commit_at))}
          </p>
        </li>
      ))}
    </ul>
  );
}

function CopyKey({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-faint hover:bg-surface-2 hover:text-fg"
      aria-label={copied ? "Copied the symbol key" : "Copy the symbol key"}
      title={copied ? "Copied" : "Copy"}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          },
          () => setCopied(false),
        );
      }}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-ok" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </button>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-fg-faint uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function NodePanel({
  snapshotId,
  nodeKey,
  actions = [],
  onAction,
  onNavigate,
  onClose,
}: NodePanelProps) {
  const api = useApi();
  const detail = useQuery({
    queryKey: queryKeys.node(snapshotId, nodeKey),
    queryFn: ({ signal }) => api.getNode(snapshotId, nodeKey, signal),
  });
  const node = detail.data?.node;
  const relation = node ? relationFor(node.kind) : null;
  const incoming = useNeighbors(snapshotId, nodeKey, "in", relation?.kind ?? null);
  const outgoing = useNeighbors(snapshotId, nodeKey, "out", relation?.kind ?? null);
  const applicable = node ? actions.filter((action) => action.appliesTo.includes(node.kind)) : [];
  const tests = detail.data?.degree.outgoing.tested_by ?? 0;

  return (
    <aside
      aria-label="Node details"
      className="flex h-full w-full flex-col border-l border-line bg-surface"
    >
      <header className="flex items-start gap-3 border-b border-line px-4 py-3">
        {node ? (
          <span
            className="ra-kind-tile mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
            data-kind={node.kind}
          >
            <KindIcon kind={node.kind} className="h-4 w-4" />
          </span>
        ) : null}
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium tracking-wide text-fg-faint uppercase">
            {node ? KIND_LABELS[node.kind] : "Loading"}
          </p>
          <h2 className="truncate text-base font-semibold text-fg" title={node?.qualified_name}>
            {node ? (node.kind === "dir" && node.name === "." ? "/" : node.name) : "…"}
          </h2>
          {node &&
          node.qualified_name !== node.name &&
          node.kind !== "dir" &&
          node.kind !== "file" ? (
            <p className="truncate font-mono text-[12px] text-fg-muted" title={node.qualified_name}>
              {node.qualified_name}
            </p>
          ) : null}
        </div>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close details"
            onClick={onClose}
            icon={<X className="h-4 w-4" aria-hidden="true" />}
          />
        ) : null}
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {detail.isPending ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
            <Spinner /> Loading details…
          </p>
        ) : detail.isError ? (
          <div role="alert" className="text-sm">
            <p className="font-medium text-fg">{describeError(detail.error).title}</p>
            <p className="mt-0.5 text-fg-muted">{describeError(detail.error).detail}</p>
          </div>
        ) : node ? (
          <>
            <dl className="space-y-2.5 text-[12px]">
              <div>
                <dt className="text-fg-faint">Path</dt>
                <dd className="mt-0.5 font-mono [overflow-wrap:anywhere] text-fg">
                  {breakable(node.path)}
                  {lineText(node) ? (
                    <span className="text-fg-muted"> · {lineText(node)}</span>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt className="text-fg-faint">Symbol key</dt>
                <dd className="mt-0.5 flex items-start gap-1">
                  <code className="min-w-0 flex-1 font-mono [overflow-wrap:anywhere] text-fg-muted">
                    {breakable(node.key)}
                  </code>
                  <CopyKey value={node.key} />
                </dd>
              </div>
            </dl>

            {relation ? (
              <Section title="Connections">
                <div className="grid grid-cols-2 gap-4">
                  <Relation title={relation.incoming} query={incoming} onNavigate={onNavigate} />
                  <Relation title={relation.outgoing} query={outgoing} onNavigate={onNavigate} />
                </div>
                {tests ? (
                  <p className="mt-3 text-[12px] text-fg-muted">
                    Tested by {tests.toLocaleString("en-US")} {tests === 1 ? "test" : "tests"}
                  </p>
                ) : null}
              </Section>
            ) : null}

            {node.kind !== "dir" ? (
              <Section title="Owners">
                <Owners owners={detail.data.owners} />
              </Section>
            ) : null}

            {applicable.length ? (
              <Section title="Actions">
                <div className="flex flex-wrap gap-2">
                  {applicable.map((action) => (
                    <Button
                      key={action.id}
                      size="sm"
                      onClick={() => onAction?.(node.key, action.id)}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              </Section>
            ) : null}

            {node.kind === "dir" ? (
              <p className="text-[12px] text-fg-muted">
                Open the folder on the map to see what it holds.
              </p>
            ) : node.meta.skip_reason ? (
              <p className="text-[12px] text-fg-muted">
                This file was skipped ({String(node.meta.skip_reason)}), so its code isn&apos;t
                stored.
              </p>
            ) : (
              <Section title="Code">
                <CodeViewer
                  snapshotId={snapshotId}
                  path={node.path}
                  startLine={node.span?.start_line ?? 1}
                  endLine={node.span?.end_line ?? 400}
                  highlightRanges={
                    node.name_range && node.kind !== "route"
                      ? [{ start: node.name_range.start_line, end: node.name_range.end_line }]
                      : []
                  }
                  className="max-h-[28rem]"
                />
              </Section>
            )}
          </>
        ) : null}
      </div>
    </aside>
  );
}
