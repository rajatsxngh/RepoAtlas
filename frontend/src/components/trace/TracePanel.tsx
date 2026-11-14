"use client";

/**
 * What one answer, impact analysis, or impact explanation did (GRD-1): its steps as a tree,
 * each with its kind, model, tokens, cost, and time, a bar showing when it ran inside the
 * trace, and the totals. The data is `GET /traces/{id}`. Span names, kinds, and error text come
 * from the server and are rendered as text.
 */

import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { SpanView, TraceView } from "@/lib/api/types";

export interface SpanNode {
  span: SpanView;
  depth: number;
  children: SpanNode[];
  /** Milliseconds from the trace's start to the span's start, and the span's length. */
  offsetMs: number;
  durationMs: number;
}

function ms(from: string, to: string | null): number {
  if (!to) return 0;
  const value = Date.parse(to) - Date.parse(from);
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

/** The trace's length: its own end, or the last span's end while it has none. */
export function traceDurationMs(trace: TraceView): number {
  const ends = trace.spans.map((span) => ms(trace.started_at, span.ended_at));
  return Math.max(ms(trace.started_at, trace.ended_at), ...ends, 0);
}

/** Spans as a forest in start order; a span whose parent is missing becomes a root. */
export function spanTree(trace: TraceView): SpanNode[] {
  const nodes = new Map<string, SpanNode>();
  for (const span of trace.spans) {
    const measured = ms(span.started_at, span.ended_at);
    const recorded = typeof span.attrs.duration_ms === "number" ? span.attrs.duration_ms : null;
    nodes.set(span.id, {
      span,
      depth: 0,
      children: [],
      offsetMs: ms(trace.started_at, span.started_at),
      // The recorded duration is finer than the timestamps (which round to the millisecond).
      durationMs: recorded ?? measured,
    });
  }
  const roots: SpanNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.span.parent_id ? nodes.get(node.span.parent_id) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  const place = (node: SpanNode, depth: number, seen: Set<string>) => {
    node.depth = depth;
    seen.add(node.span.id);
    node.children = node.children.filter((child) => !seen.has(child.span.id));
    for (const child of node.children) place(child, depth + 1, seen);
  };
  const seen = new Set<string>();
  for (const root of roots) place(root, 0, seen);
  return roots;
}

export function formatDuration(value: number): string {
  if (value < 1) return "<1 ms";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`;
}

export function formatTokens(value: number): string {
  if (value < 1000) return String(value);
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
}

export function formatCost(value: number): string {
  return `$${value.toFixed(4)}`;
}

function SpanRow({
  node,
  total,
  collapsed,
  onToggle,
}: {
  node: SpanNode;
  total: number;
  collapsed: boolean;
  onToggle: (id: string) => void;
}) {
  const { span } = node;
  const hasChildren = node.children.length > 0;
  const left = total > 0 ? Math.min(100, (node.offsetMs / total) * 100) : 0;
  const width =
    total > 0 ? Math.max(1.5, Math.min(100 - left, (node.durationMs / total) * 100)) : 100;
  const failed = span.status === "error";
  const error = failed && typeof span.attrs.error === "string" ? span.attrs.error : null;
  const tokens =
    span.tokens_in !== null || span.tokens_out !== null
      ? `${formatTokens(span.tokens_in ?? 0)} in · ${formatTokens(span.tokens_out ?? 0)} out`
      : null;
  return (
    <li
      role="treeitem"
      aria-level={node.depth + 1}
      aria-expanded={hasChildren ? !collapsed : undefined}
      aria-selected={false}
      data-span-kind={span.kind}
      className="py-1"
    >
      <div className="flex items-center gap-1.5" style={{ paddingLeft: `${node.depth * 14}px` }}>
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(span.id)}
            aria-label={`${collapsed ? "Expand" : "Collapse"} ${span.name}`}
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            {collapsed ? (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        ) : (
          <span className="h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <span
          className={`min-w-0 flex-1 truncate font-mono text-xs ${failed ? "text-danger" : "text-fg"}`}
          title={span.name}
        >
          {span.name}
        </span>
        <span className="shrink-0 rounded border border-line bg-surface-2 px-1 text-[10px] text-fg-muted">
          {span.kind}
        </span>
        <span className="w-14 shrink-0 text-right font-mono text-[11px] text-fg-muted tabular-nums">
          {formatDuration(node.durationMs)}
        </span>
      </div>
      <div style={{ paddingLeft: `${node.depth * 14 + 22}px` }}>
        <div
          className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2"
          role="img"
          aria-label={`${span.name}: starts at ${formatDuration(node.offsetMs)}, takes ${formatDuration(node.durationMs)}`}
        >
          <div
            data-testid="span-bar"
            className={`h-full rounded-full ${failed ? "bg-danger" : span.kind === "llm" ? "bg-focus" : "bg-fg-faint"}`}
            style={{ marginLeft: `${left}%`, width: `${width}%` }}
          />
        </div>
        {span.model || tokens || span.cost_usd !== null ? (
          <p className="mt-0.5 truncate text-[11px] text-fg-muted">
            {[span.model, tokens, span.cost_usd !== null ? formatCost(span.cost_usd) : null]
              .filter(Boolean)
              .join(" · ")}
          </p>
        ) : null}
        {error ? <p className="mt-0.5 text-[11px] break-words text-danger">{error}</p> : null}
      </div>
      {hasChildren && !collapsed ? (
        <ul role="group">
          {node.children.map((child) => (
            <SpanBranch key={child.span.id} node={child} total={total} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function SpanBranch({ node, total }: { node: SpanNode; total: number }) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <SpanRow
      node={node}
      total={total}
      collapsed={collapsed}
      onToggle={() => setCollapsed((value) => !value)}
    />
  );
}

export interface TracePanelProps {
  traceId: string;
  className?: string;
}

export function TracePanel({ traceId, className = "" }: TracePanelProps) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["trace", traceId],
    queryFn: ({ signal }) => api.getTrace(traceId, signal),
    staleTime: Infinity,
    retry: false,
  });
  const trace = query.data;
  const tree = useMemo(() => (trace ? spanTree(trace) : []), [trace]);
  const total = trace ? traceDurationMs(trace) : 0;
  const failure = query.error ? describeError(query.error) : null;
  return (
    <section
      aria-label="Trace"
      className={`rounded-lg border border-line bg-surface px-3 py-2 ${className}`}
    >
      <h3 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-fg-muted uppercase">
        <Activity className="h-3.5 w-3.5" aria-hidden="true" />
        Trace
      </h3>
      {query.isPending ? (
        <p role="status" className="mt-2 flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Loading the trace…
        </p>
      ) : null}
      {failure ? (
        <div role="alert" className="mt-2 text-sm">
          <p className="font-medium text-fg">{failure.title}</p>
          <p className="mt-0.5 text-fg-muted">{failure.detail}</p>
        </div>
      ) : null}
      {trace ? (
        <>
          <dl
            data-testid="trace-totals"
            className="mt-2 grid grid-cols-4 gap-2 text-[11px] text-fg-muted"
          >
            <div>
              <dt>Time</dt>
              <dd className="font-mono text-sm text-fg tabular-nums">{formatDuration(total)}</dd>
            </div>
            <div>
              <dt>Tokens in</dt>
              <dd className="font-mono text-sm text-fg tabular-nums">
                {formatTokens(trace.tokens_in)}
              </dd>
            </div>
            <div>
              <dt>Tokens out</dt>
              <dd className="font-mono text-sm text-fg tabular-nums">
                {formatTokens(trace.tokens_out)}
              </dd>
            </div>
            <div>
              <dt>Cost</dt>
              <dd className="font-mono text-sm text-fg tabular-nums">
                {formatCost(trace.cost_usd)}
              </dd>
            </div>
          </dl>
          <p className="mt-1 text-[11px] text-fg-faint">
            {trace.kind} · {trace.spans.length} {trace.spans.length === 1 ? "step" : "steps"}
            {trace.status === "error" ? " · failed" : ""}
          </p>
          {tree.length > 0 ? (
            <ul role="tree" aria-label="Steps" className="mt-2 divide-y divide-line">
              {tree.map((node) => (
                <SpanBranch key={node.span.id} node={node} total={total} />
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-fg-faint">This trace recorded no steps.</p>
          )}
        </>
      ) : null}
    </section>
  );
}

export interface TraceToggleProps {
  traceId: string | null | undefined;
  /** What the run cost; shown on the badge when given. */
  costUsd?: number;
  /** Total tokens, in and out; shown on the badge when given. */
  tokens?: number;
  label?: string;
}

/**
 * The cost badge on an answer (or the trace button on a report): pressing it shows the run's
 * trace underneath. A run without a trace shows the badge alone.
 */
export function TraceToggle({ traceId, costUsd, tokens, label = "Trace" }: TraceToggleProps) {
  const [open, setOpen] = useState(false);
  const parts = [
    costUsd !== undefined ? formatCost(costUsd) : null,
    tokens !== undefined ? `${formatTokens(tokens)} tokens` : null,
  ].filter(Boolean);
  const text = [...parts, traceId ? label : null].filter(Boolean).join(" · ");
  if (!traceId) {
    return parts.length > 0 ? (
      <span
        data-testid="cost-badge"
        className="inline-flex rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted"
      >
        {text}
      </span>
    ) : null;
  }
  return (
    <div className="space-y-2">
      <button
        type="button"
        data-testid="cost-badge"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title="Steps, tokens, cost, and time of this run"
        className="inline-flex items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted hover:border-line-strong hover:text-fg"
      >
        <Activity className="h-3 w-3" aria-hidden="true" />
        {text}
      </button>
      {open ? <TracePanel traceId={traceId} /> : null}
    </div>
  );
}
