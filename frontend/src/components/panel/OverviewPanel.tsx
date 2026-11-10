"use client";

/**
 * "Start here": what a reader opening a repository wants first — its public API and routes, the files and
 * symbols the rest of the code leans on, its size, and how much of it has linked tests. Every
 * item opens on the map. Every name is repo text, rendered as text.
 */

import { useQuery } from "@tanstack/react-query";
import { Compass, X } from "lucide-react";
import { KindIcon } from "@/components/graph/KindIcon";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { ApiNode, Overview, Ranked } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface OverviewPanelProps {
  snapshotId: number;
  onNavigate?: (nodeKey: string) => void;
  onClose?: () => void;
}

const COUNTED: { kind: string; one: string; many: string }[] = [
  { kind: "file", one: "file", many: "files" },
  { kind: "class", one: "class", many: "classes" },
  { kind: "function", one: "function", many: "functions" },
  { kind: "method", one: "method", many: "methods" },
  { kind: "test", one: "test", many: "tests" },
  { kind: "route", one: "route", many: "routes" },
];

export function sizeLine(counts: Overview["counts"]): string {
  return COUNTED.filter(({ kind }) => counts[kind])
    .map(({ kind, one, many }) => {
      const count = counts[kind]!;
      return `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
    })
    .join(" · ");
}

function itemLabel(node: ApiNode): string {
  if (node.kind === "file") return node.path;
  if (node.kind === "route") return node.name;
  return node.qualified_name;
}

function Item({
  node,
  detail,
  onNavigate,
}: {
  node: ApiNode;
  detail?: string;
  onNavigate?: (key: string) => void;
}) {
  const label = itemLabel(node);
  return (
    <li>
      <button
        type="button"
        className="flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-surface-2"
        title={`${label} (${node.path})`}
        onClick={() => onNavigate?.(node.key)}
      >
        <KindIcon kind={node.kind} className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-fg">{label}</span>
        {detail ? (
          <span className="shrink-0 text-[11px] text-fg-faint tabular-nums">{detail}</span>
        ) : null}
      </button>
    </li>
  );
}

function usedBy(item: Ranked): string {
  return `${item.files.toLocaleString("en-US")} ${item.files === 1 ? "file" : "files"}`;
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="text-[11px] font-semibold tracking-wide text-fg-faint uppercase">{title}</h3>
      <p className="mb-1.5 text-[12px] text-fg-muted">{hint}</p>
      <ul>{children}</ul>
    </section>
  );
}

export function OverviewPanel({ snapshotId, onNavigate, onClose }: OverviewPanelProps) {
  const api = useApi();
  const found = useQuery({
    queryKey: queryKeys.overview(snapshotId),
    queryFn: ({ signal }) => api.getOverview(snapshotId, signal),
  });
  const data = found.data;
  const tested =
    data && data.source_files ? Math.round((100 * data.tested_files) / data.source_files) : null;

  return (
    <aside
      aria-label="Start here"
      className="flex h-full w-full flex-col border-l border-line bg-surface"
    >
      <header className="flex items-start gap-3 border-b border-line px-4 py-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-accent">
          <Compass className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium tracking-wide text-fg-faint uppercase">Overview</p>
          <h2 className="text-base font-semibold text-fg">Start here</h2>
          {data ? <p className="text-[12px] text-fg-muted">{sizeLine(data.counts)}</p> : null}
        </div>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close overview"
            onClick={onClose}
            icon={<X className="h-4 w-4" aria-hidden="true" />}
          />
        ) : null}
      </header>
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {found.isPending ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
            <Spinner /> Reading the repository…
          </p>
        ) : found.isError ? (
          <div role="alert" className="text-sm">
            <p className="font-medium text-fg">{describeError(found.error).title}</p>
            <p className="mt-0.5 text-fg-muted">{describeError(found.error).detail}</p>
          </div>
        ) : data ? (
          <>
            {data.public_symbols.length ? (
              <Section title="Public API" hint="What the package offers its users.">
                {data.public_symbols.map((node) => (
                  <Item key={node.key} node={node} onNavigate={onNavigate} />
                ))}
              </Section>
            ) : null}
            {data.routes.length ? (
              <Section
                title="Routes"
                hint="Where requests come in, and the file that handles them."
              >
                {data.routes.map((node) => (
                  <Item
                    key={node.key}
                    node={node}
                    detail={node.path.split("/").slice(-2).join("/")}
                    onNavigate={onNavigate}
                  />
                ))}
              </Section>
            ) : null}
            {data.core_files.length ? (
              <Section
                title="Core files"
                hint="Imported and called the most from other files. Read these first."
              >
                {data.core_files.map((item) => (
                  <Item
                    key={item.node.key}
                    node={item.node}
                    detail={usedBy(item)}
                    onNavigate={onNavigate}
                  />
                ))}
              </Section>
            ) : null}
            {data.core_symbols.length ? (
              <Section
                title="Most used code"
                hint="Classes and functions other files depend on: change them with care."
              >
                {data.core_symbols.map((item) => (
                  <Item
                    key={item.node.key}
                    node={item.node}
                    detail={usedBy(item)}
                    onNavigate={onNavigate}
                  />
                ))}
              </Section>
            ) : null}
            {tested !== null ? (
              <section>
                <h3 className="text-[11px] font-semibold tracking-wide text-fg-faint uppercase">
                  Tests
                </h3>
                <p className="mt-1 text-[13px] text-fg">
                  {tested}% of source files have linked tests
                </p>
                <div
                  className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3"
                  role="meter"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={tested}
                  aria-label="Source files with linked tests"
                >
                  <div className="h-full rounded-full bg-accent" style={{ width: `${tested}%` }} />
                </div>
                <p className="mt-1 text-[12px] text-fg-muted">
                  {data.tested_files.toLocaleString("en-US")} of{" "}
                  {data.source_files.toLocaleString("en-US")}, by imports, calls, and file names.
                </p>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </aside>
  );
}
