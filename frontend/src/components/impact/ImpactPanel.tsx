"use client";

/**
 * "What breaks?" (IMP-7): the ranked, grouped impact of changing one symbol. Items are grouped
 * into tests, public APIs, and callers, each ranked by score with its best path; unknown sites
 * (calls the graph could not resolve, string literals naming the symbol) are listed apart and
 * never counted as safe. Clicking an item or a site opens its evidence in the code viewer and
 * tells the workspace to show it on the map. Names and paths are repo text, rendered as text.
 */

import { useQuery } from "@tanstack/react-query";
import { CircleHelp, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { CodeViewer } from "@/components/code/CodeViewer";
import type { HighlightLayer } from "@/components/graph/layers";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type {
  EvidenceRef,
  ImpactGroup,
  ImpactItem,
  ImpactResponse,
  PathStep,
  UnknownSite,
} from "@/lib/api/types";

export interface ImpactPanelProps {
  snapshotId: number;
  symbolKey: string;
  /** The report arrived (or was cleared with `null`): the workspace draws its layer. */
  onReport?: (report: ImpactResponse | null) => void;
  /** An item or unknown site was opened: show this symbol on the map. */
  onOpen?: (symbolKey: string) => void;
  onClose?: () => void;
  className?: string;
}

const GROUPS: readonly { id: ImpactGroup; title: string; empty: string }[] = [
  { id: "test", title: "Tests", empty: "No tests reach this symbol through the graph." },
  { id: "public_api", title: "Public APIs", empty: "No public API depends on it." },
  { id: "caller", title: "Callers", empty: "Nothing else depends on it." },
];

const REASONS: Record<UnknownSite["reason"], string> = {
  ambiguous: "too many candidates",
  not_found: "callee not found",
  unbound_name: "unbound name",
  dynamic: "dynamic call",
  string_literal: "string literal",
};

const STEP_VERB: Record<PathStep["kind"], string> = {
  calls: "called by",
  inherits: "inherited by",
  imports: "imported by",
  tested_by: "tested by",
  routes_to: "routed from",
  overrides: "overridden by",
  contains: "in",
};

const CONTEXT_LINES = 3;

export function symbolLabel(key: string): string {
  const parts = key.split(":");
  if (parts.length >= 4) return decodeURIComponent(parts[2]!);
  return decodeURIComponent(parts.slice(1).join(":") || key);
}

export function evidenceLabel(ref: EvidenceRef): string {
  const name = ref.path.split("/").at(-1) ?? ref.path;
  return ref.start_line === ref.end_line
    ? `${name}:${ref.start_line}`
    : `${name}:${ref.start_line}–${ref.end_line}`;
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function PathText({ path }: { path: PathStep[] }) {
  if (path.length === 0) return null;
  return (
    <p className="mt-0.5 truncate text-[11px] text-fg-faint" data-testid="impact-path">
      {path.map((step, index) => (
        <span key={`${step.src}|${step.dst}|${index}`}>
          {index === 0 ? symbolLabel(step.src) : ""} {STEP_VERB[step.kind]} {symbolLabel(step.dst)}
        </span>
      ))}
    </p>
  );
}

function ItemRow({
  item,
  active,
  onPick,
}: {
  item: ImpactItem;
  active: boolean;
  onPick: (item: ImpactItem) => void;
}) {
  return (
    <li>
      <button
        type="button"
        aria-pressed={active}
        onClick={() => onPick(item)}
        title={item.symbol_key}
        className={`w-full rounded-lg border px-3 py-2 text-left ${
          active ? "border-focus bg-focus/10" : "border-line hover:bg-surface-2"
        }`}
      >
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate font-mono text-sm text-fg">{item.name}</span>
          <span
            className="font-mono text-[11px] text-fg-muted tabular-nums"
            aria-label={`score ${percent(item.score)}`}
          >
            {percent(item.score)}
          </span>
        </span>
        <span
          className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-2"
          aria-hidden="true"
        >
          <span
            className="block h-full rounded-full bg-amber-500"
            style={{ width: percent(Math.max(item.score, 0.02)) }}
          />
        </span>
        <span className="mt-1 block truncate text-[11px] text-fg-muted">
          {item.path} · {item.depth === 1 ? "direct" : `${item.depth} steps`}
        </span>
        <PathText path={item.best_path} />
      </button>
    </li>
  );
}

type Opened = { kind: "item"; key: string; ref: EvidenceRef } | { kind: "site"; ref: EvidenceRef };

export function ImpactPanel({
  snapshotId,
  symbolKey,
  onReport,
  onOpen,
  onClose,
  className = "",
}: ImpactPanelProps) {
  const api = useApi();
  const [opened, setOpened] = useState<Opened | null>(null);
  const query = useQuery({
    queryKey: ["impact", snapshotId, symbolKey],
    queryFn: ({ signal }) =>
      api.analyzeImpact(snapshotId, { symbol_key: symbolKey, side: "old" }, signal),
    staleTime: Infinity,
    retry: false,
  });
  const report = query.data;
  useEffect(() => {
    onReport?.(report ?? null);
  }, [onReport, report]);
  const failure = query.error ? describeError(query.error) : null;

  const pickItem = (item: ImpactItem) => {
    const ref = item.evidence[0];
    setOpened(ref ? { kind: "item", key: item.symbol_key, ref } : null);
    onOpen?.(item.symbol_key);
  };
  const pickSite = (site: UnknownSite) => {
    setOpened({ kind: "site", ref: site.site });
    if (site.site.symbol_key) onOpen?.(site.site.symbol_key);
  };

  return (
    <aside
      aria-label="What breaks"
      className={`flex h-full flex-col overflow-hidden border-l border-line bg-surface ${className}`}
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Zap className="h-4 w-4 text-fg-muted" aria-hidden="true" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
          What breaks if <span className="font-mono">{symbolLabel(symbolKey)}</span> changes?
        </h2>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close impact"
            icon={<X className="h-4 w-4" aria-hidden="true" />}
          />
        ) : null}
      </header>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {query.isPending ? (
          <p role="status" className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Following what depends on it…
          </p>
        ) : null}
        {failure ? (
          <div role="alert" className="rounded-lg border border-line px-3 py-2 text-sm">
            <p className="font-medium text-fg">{failure.title}</p>
            <p className="mt-0.5 text-fg-muted">{failure.detail}</p>
          </div>
        ) : null}
        {report ? (
          <>
            <p className="text-[11px] text-fg-faint" data-testid="impact-stats">
              {report.stats.items} affected
              {report.stats.truncated ? ` (top ${report.stats.returned} shown)` : ""} ·{" "}
              {report.unknown.length} unknown · scores combine every path (decay{" "}
              {report.params.decay}, depth ≤ {report.params.max_depth})
            </p>
            {GROUPS.map((group) => {
              const items = report.items.filter((item) => item.group === group.id);
              return (
                <section key={group.id} aria-label={group.title} className="space-y-2">
                  <h3 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">
                    {group.title} <span className="font-normal">({items.length})</span>
                  </h3>
                  {items.length === 0 ? (
                    <p className="text-xs text-fg-faint">{group.empty}</p>
                  ) : (
                    <ol className="space-y-1.5">
                      {items.map((item) => (
                        <ItemRow
                          key={item.symbol_key}
                          item={item}
                          active={opened?.kind === "item" && opened.key === item.symbol_key}
                          onPick={pickItem}
                        />
                      ))}
                    </ol>
                  )}
                </section>
              );
            })}
            <section aria-label="Unknown" className="space-y-2">
              <h3 className="flex items-center gap-1 text-xs font-semibold tracking-wide text-fg-muted uppercase">
                <CircleHelp className="h-3.5 w-3.5" aria-hidden="true" />
                Unknown <span className="font-normal">({report.unknown.length})</span>
              </h3>
              <p className="text-xs text-fg-faint">
                {report.unknown.length === 0
                  ? "No unresolved calls or string literals name this symbol."
                  : "The graph can't tell whether these depend on it. They are not safe — check them."}
              </p>
              {report.unknown.length > 0 ? (
                <ol className="space-y-1.5">
                  {report.unknown.map((site) => (
                    <li key={`${site.site.path}|${site.site.start_line}|${site.callee_text}`}>
                      <button
                        type="button"
                        onClick={() => pickSite(site)}
                        className="w-full rounded-lg border border-dashed border-line px-3 py-1.5 text-left hover:bg-surface-2"
                      >
                        <span className="block truncate font-mono text-xs text-fg">
                          {site.callee_text}
                        </span>
                        <span className="block truncate text-[11px] text-fg-muted">
                          {evidenceLabel(site.site)} · {REASONS[site.reason]}
                        </span>
                      </button>
                    </li>
                  ))}
                </ol>
              ) : null}
            </section>
          </>
        ) : null}
        {opened ? (
          <CodeViewer
            key={`${opened.ref.path}|${opened.ref.start_line}|${opened.ref.end_line}`}
            snapshotId={snapshotId}
            path={opened.ref.path}
            startLine={Math.max(1, opened.ref.start_line - CONTEXT_LINES)}
            endLine={opened.ref.end_line + CONTEXT_LINES}
            highlightRanges={[{ start: opened.ref.start_line, end: opened.ref.end_line }]}
            className="max-h-80"
          />
        ) : null}
      </div>
    </aside>
  );
}

export const IMPACT_LAYER_ID = "impact";
export const IMPACT_TARGET_LAYER_ID = "impact-target";

/** The scored layer of affected symbols, and the changed symbol itself. */
export function impactLayers(report: ImpactResponse): HighlightLayer[] {
  const scores: Record<string, number> = {};
  for (const item of report.items) scores[item.symbol_key] = item.score;
  const name = report.targets.length === 1 ? symbolLabel(report.targets[0]!) : "the change";
  return [
    {
      id: IMPACT_TARGET_LAYER_ID,
      nodeKeys: [...report.targets],
      style: "primary",
      label: `Changed: ${name}`,
    },
    {
      id: IMPACT_LAYER_ID,
      nodeKeys: report.items.map((item) => item.symbol_key),
      style: "warning",
      scores,
      label: `Affected by ${name}`,
    },
  ];
}
