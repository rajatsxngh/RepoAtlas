"use client";

/**
 * What one answer, impact analysis, or impact explanation did (GRD-1): its steps as a tree,
 * each with its kind, model, tokens, cost, and time, a bar showing when it ran inside the
 * trace, and the totals. The data is `GET /traces/{id}`. Span names, kinds, and error text come
 * from the server and are rendered as text.
 *
 * `LiveSteps` shows an agent answer's tool calls while it is being made (AGT-6): one row per
 * `step` event of the ask stream, as it arrives. Tool names, arguments, and errors are model
 * or repo text, rendered as text.
 *
 * A multi-agent answer has branches (MAS-6). `LiveSteps` groups its steps by branch, and the
 * trace's tree shows each branch's span as a labeled group with the branch's steps under it.
 */

import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { AskPlan, AskStepEvent, BranchSummary, SpanView, TraceView } from "@/lib/api/types";

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
  // A multi-agent answer's branch: its spans are this span's children, so the branch is one
  // group of the tree. The label says which branch and how it ended.
  const branch =
    span.name === "multiagent.branch" && typeof span.attrs.branch === "string"
      ? span.attrs.branch
      : null;
  const branchStatus = branch && typeof span.attrs.status === "string" ? span.attrs.status : null;
  const label = branch ? `branch ${branch}` : span.name;
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
      data-branch={branch ?? undefined}
      className="py-1"
    >
      <div className="flex items-center gap-1.5" style={{ paddingLeft: `${node.depth * 14}px` }}>
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(span.id)}
            aria-label={`${collapsed ? "Expand" : "Collapse"} ${label}`}
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
          {label}
        </span>
        {branchStatus ? (
          <span
            data-testid="branch-status"
            className={`shrink-0 text-[10px] ${branchStatus === "complete" ? "text-fg-muted" : "text-danger"}`}
          >
            {branchStatus}
          </span>
        ) : null}
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

const STEP_TITLE: Record<AskStepEvent["kind"], string | null> = {
  tool: null,
  finish: "Finished exploring",
  answer: "Wrote the answer",
  plan: "Planned the exploration",
  notes: "Reported its findings",
  write: "Wrote the answer",
  verify: "Checked the claims against the code",
  repair: "Rewrote partly supported claims",
};

function stepNotes(event: AskStepEvent): string[] {
  const notes: string[] = [];
  if (event.kind === "plan" && event.arguments) notes.push(event.arguments);
  if (event.kind !== "tool") return notes;
  if (event.cached) {
    notes.push(
      event.branch
        ? "already run for this answer, not run again"
        : "same call as before, not run again",
    );
  }
  if (!event.error) {
    notes.push(`${event.evidence} ${event.evidence === 1 ? "piece" : "pieces"} of evidence`);
    notes.push(`about ${formatTokens(event.result_tokens)} tokens returned`);
  }
  if (event.truncated) notes.push("cut to fit");
  if (event.flags.length > 0) notes.push("contains instruction-like text, marked as data");
  return notes;
}

function StepRow({ event }: { event: AskStepEvent }) {
  const refused = !event.allowed;
  const notes = stepNotes(event);
  const usage = event.model
    ? [
        event.model,
        `${formatTokens(event.tokens_in)} in · ${formatTokens(event.tokens_out)} out`,
        formatCost(event.cost_usd),
      ].join(" · ")
    : null;
  return (
    <li data-step-kind={event.kind} data-refused={refused ? "true" : undefined} className="py-1.5">
      <p className="flex items-baseline gap-1.5 text-xs">
        <span className="w-4 shrink-0 text-right font-mono text-[10px] text-fg-faint tabular-nums">
          {event.seq}
        </span>
        {STEP_TITLE[event.kind] ? (
          <span className="text-fg">{STEP_TITLE[event.kind]}</span>
        ) : (
          <>
            <span className={`shrink-0 font-mono ${event.error ? "text-danger" : "text-fg"}`}>
              {event.tool}
            </span>
            <span className="min-w-0 truncate font-mono text-fg-muted" title={event.arguments}>
              {event.arguments}
            </span>
          </>
        )}
      </p>
      <div className="pl-[22px]">
        {event.error ? (
          <p className="text-[11px] break-words text-danger">
            {refused ? "Refused: " : "Failed: "}
            {event.error}
          </p>
        ) : null}
        {notes.length > 0 ? <p className="text-[11px] text-fg-muted">{notes.join(" · ")}</p> : null}
        {usage ? <p className="truncate text-[11px] text-fg-faint">{usage}</p> : null}
      </div>
    </li>
  );
}

const BRANCH_STATUS: Record<BranchSummary["status"], string> = {
  complete: "explored",
  partial: "stopped at a limit",
  timeout: "ran out of time",
  failed: "failed",
};

export interface StepGroup {
  /** The branch's ID, or `null` for steps of the whole answer (plan, write, verify). */
  branch: string | null;
  steps: AskStepEvent[];
}

/**
 * The steps of an answer in the order to show them: the steps before any branch, one group per
 * branch (in the plan's order, then any branch the plan didn't list, by first appearance), and
 * the steps after the branches. Branches run at the same time, so their events arrive mixed;
 * grouping keeps each branch's steps together.
 */
export function groupSteps(steps: readonly AskStepEvent[], plan?: AskPlan | null): StepGroup[] {
  const order: string[] = (plan?.branches ?? []).map((branch) => branch.id);
  for (const event of steps) {
    if (event.branch && !order.includes(event.branch)) order.push(event.branch);
  }
  if (order.length === 0) return [{ branch: null, steps: [...steps] }];
  const firstBranch = steps.findIndex((event) => event.branch !== null);
  const before = steps.filter(
    (event, index) => event.branch === null && (firstBranch < 0 || index < firstBranch),
  );
  const after = steps.filter(
    (event, index) => event.branch === null && firstBranch >= 0 && index > firstBranch,
  );
  const groups: StepGroup[] = [];
  if (before.length > 0) groups.push({ branch: null, steps: before });
  for (const branch of order) {
    groups.push({ branch, steps: steps.filter((event) => event.branch === branch) });
  }
  if (after.length > 0) groups.push({ branch: null, steps: after });
  return groups;
}

export interface LiveStepsProps {
  /** The `step` events received so far, in order. */
  steps: readonly AskStepEvent[];
  /** Multi-agent mode: the plan's branches, once known. */
  plan?: AskPlan | null;
  /** Multi-agent mode: how each branch ended, once the answer is there. */
  branches?: readonly BranchSummary[];
  /** The answer is still being made. */
  running?: boolean;
  className?: string;
}

/**
 * An answer's steps as they happen: each tool call, then the final answer call. A multi-agent
 * answer's steps are grouped by branch, each under its sub-question (model text, rendered as
 * text).
 */
export function LiveSteps({
  steps,
  plan = null,
  branches = [],
  running = false,
  className = "",
}: LiveStepsProps) {
  const calls = steps.filter((event) => event.kind === "tool").length;
  const cost = steps.reduce((sum, event) => sum + event.cost_usd, 0);
  const groups = groupSteps(steps, plan);
  const branchCount = groups.filter((group) => group.branch !== null).length;
  return (
    <section
      aria-label="Agent steps"
      aria-busy={running}
      className={`rounded-lg border border-line bg-surface px-3 py-2 ${className}`}
    >
      <h3 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-fg-muted uppercase">
        <Activity className="h-3.5 w-3.5" aria-hidden="true" />
        Steps
        <span
          data-testid="steps-summary"
          className="ml-auto font-mono text-[11px] font-normal tracking-normal normal-case"
        >
          {branchCount > 0 ? `${branchCount} ${branchCount === 1 ? "branch" : "branches"} · ` : ""}
          {calls} {calls === 1 ? "tool call" : "tool calls"} · {formatCost(cost)}
        </span>
      </h3>
      {branchCount > 0 ? (
        <div className="mt-1 space-y-1.5">
          {groups.map((group, index) => {
            if (group.branch === null) {
              return (
                <ol
                  key={`stage-${index}`}
                  aria-label={index === 0 ? "Before the branches" : "After the branches"}
                  className="divide-y divide-line"
                >
                  {group.steps.map((event) => (
                    <StepRow key={event.seq} event={event} />
                  ))}
                </ol>
              );
            }
            const planned = plan?.branches.find((branch) => branch.id === group.branch);
            const ended = branches.find((branch) => branch.id === group.branch);
            const reported = group.steps.some((event) => event.kind === "notes");
            const state = ended
              ? BRANCH_STATUS[ended.status]
              : running
                ? reported
                  ? "explored"
                  : group.steps.length > 0
                    ? "exploring…"
                    : "waiting…"
                : null;
            return (
              <section
                key={group.branch}
                aria-label={`Branch ${group.branch}`}
                data-branch={group.branch}
                data-branch-status={ended?.status}
                className="rounded-md border border-line bg-surface-2/50 px-2 py-1"
              >
                <h4 className="flex items-baseline gap-1.5 text-xs">
                  <span className="shrink-0 rounded border border-line bg-surface px-1 font-mono text-[10px] text-fg-muted">
                    {group.branch}
                  </span>
                  <span className="min-w-0 flex-1 break-words text-fg">
                    {planned?.sub_question ?? ended?.sub_question ?? "Branch"}
                  </span>
                  {state ? (
                    <span
                      data-testid="branch-state"
                      className={`shrink-0 text-[11px] ${
                        ended && ended.status !== "complete" ? "text-danger" : "text-fg-muted"
                      }`}
                    >
                      {state}
                    </span>
                  ) : null}
                </h4>
                {group.steps.length > 0 ? (
                  <ol
                    aria-label={`Steps of branch ${group.branch}`}
                    className="divide-y divide-line"
                  >
                    {group.steps.map((event) => (
                      <StepRow key={event.seq} event={event} />
                    ))}
                  </ol>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : (
        <ol aria-label="Agent steps" className="mt-1 divide-y divide-line">
          {steps.map((event) => (
            <StepRow key={event.seq} event={event} />
          ))}
        </ol>
      )}
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
