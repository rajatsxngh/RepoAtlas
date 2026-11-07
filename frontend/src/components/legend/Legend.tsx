"use client";

import { ChevronDown, ChevronUp, Info } from "lucide-react";
import { useId, useState } from "react";
import { KIND_LABELS, KindIcon } from "@/components/graph/KindIcon";
import { EDGE_LABELS, GUESS_DASH, LOW_CONFIDENCE } from "@/components/graph/edgeStyle";
import type { HighlightLayer, HighlightStyle } from "@/components/graph/layers";
import type { EdgeKind, NodeKind } from "@/lib/api/types";

const NODE_KINDS: NodeKind[] = ["dir", "file", "class", "function", "method", "route", "test"];
const EDGE_KINDS: EdgeKind[] = ["calls", "imports", "inherits", "tested_by", "routes_to"];

export const HIGHLIGHT_COLORS: Record<HighlightStyle, string> = {
  primary: "var(--ra-hl-primary)",
  secondary: "var(--ra-hl-secondary)",
  warning: "var(--ra-hl-warning)",
  muted: "var(--ra-hl-muted)",
};

function Line({ color, dash, width = 2 }: { color: string; dash?: string; width?: number }) {
  return (
    <svg width="28" height="10" viewBox="0 0 28 10" aria-hidden="true" className="shrink-0">
      <line
        x1="1"
        y1="5"
        x2="21"
        y2="5"
        stroke={color}
        strokeWidth={width}
        strokeDasharray={dash}
      />
      <path d="M 20 1.5 L 27 5 L 20 8.5 z" fill={color} />
    </svg>
  );
}

export interface LegendProps {
  layers?: readonly HighlightLayer[];
  defaultOpen?: boolean;
}

export function Legend({ layers = [], defaultOpen = false }: LegendProps) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  return (
    <section
      aria-label="Legend"
      className="pointer-events-auto w-64 overflow-hidden rounded-xl border border-line bg-surface/95 text-fg shadow-pop backdrop-blur"
    >
      <button
        type="button"
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-[13px] font-medium hover:bg-surface-2"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <Info className="h-4 w-4 text-fg-faint" aria-hidden="true" />
        <span className="flex-1">Legend</span>
        {layers.length ? (
          <span className="rounded-full bg-surface-3 px-1.5 text-[11px] text-fg-muted tabular-nums">
            {layers.length} {layers.length === 1 ? "layer" : "layers"}
          </span>
        ) : null}
        {open ? (
          <ChevronDown className="h-4 w-4 text-fg-faint" aria-hidden="true" />
        ) : (
          <ChevronUp className="h-4 w-4 text-fg-faint" aria-hidden="true" />
        )}
      </button>
      {open ? (
        <div id={panelId} className="space-y-3 border-t border-line px-3 pt-2.5 pb-3 text-[12px]">
          <div>
            <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-fg-faint uppercase">
              Nodes
            </h3>
            <ul className="grid grid-cols-2 gap-x-3 gap-y-1">
              {NODE_KINDS.map((kind) => (
                <li key={kind} className="flex items-center gap-2 text-fg-muted">
                  <KindIcon kind={kind} className="h-3.5 w-3.5" />
                  {KIND_LABELS[kind]}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-fg-faint uppercase">
              Edges
            </h3>
            <ul className="space-y-1">
              {EDGE_KINDS.map((kind) => (
                <li key={kind} className="flex items-center gap-2 text-fg-muted">
                  <Line color={`var(--ra-edge-${kind.replace("_", "-")})`} />
                  {EDGE_LABELS[kind].legend}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-fg-faint uppercase">
              Confidence and bundles
            </h3>
            <ul className="space-y-1 text-fg-muted">
              <li className="flex items-center gap-2">
                <Line color="var(--ra-fg-muted)" />
                Resolved
              </li>
              <li className="flex items-center gap-2">
                <Line color="var(--ra-fg-muted)" dash={GUESS_DASH} />
                Guessed by name (below {LOW_CONFIDENCE})
              </li>
              <li className="flex items-center gap-2">
                <Line color="var(--ra-fg-muted)" width={4.5} />
                Wider and numbered: many edges
              </li>
            </ul>
          </div>
          {layers.length ? (
            <div>
              <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-fg-faint uppercase">
                Highlights
              </h3>
              <ul className="space-y-1">
                {layers.map((layer) => (
                  <li key={layer.id} className="flex items-center gap-2 text-fg-muted">
                    <span
                      className="h-3 w-3 shrink-0 rounded-full"
                      style={{ background: HIGHLIGHT_COLORS[layer.style] }}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate">{layer.label ?? layer.id}</span>
                    <span className="tabular-nums">{layer.nodeKeys.length}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
