"use client";

/**
 * A validated answer as the panels show it: the refusal (if any), the markdown through the
 * sanitized renderer, each claim with its citations as chips, the measured numbers, and a cost
 * badge that opens the run's trace. Used for answers to questions and for explanations of
 * impact reports. Claims and paths are repo or model text, rendered as text.
 *
 * A citation whose code the injection detector flagged (GRD-3) shows a warning icon: the code
 * contains text that looks written to steer an AI, and it was given to the model as data.
 */

import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { TraceToggle } from "@/components/trace/TracePanel";
import type { AnswerView, Citation, RefusalKind } from "@/lib/api/types";
import { Markdown } from "@/lib/markdown";

const REFUSAL_TITLE: Record<RefusalKind, string> = {
  evidence: "Not enough evidence",
  api: "The model declined",
  budget: "Over the cost budget",
};

export function citationLabel(citation: Citation): string {
  const { path, start_line: start, end_line: end } = citation.ref;
  const name = path.split("/").at(-1) ?? path;
  return start === end ? `${name}:${start}` : `${name}:${start}–${end}`;
}

/** What the injection detector found, in words. Unknown kinds (from a newer API) read as is. */
const FLAG_TEXT: Record<string, string> = {
  instruction_to_assistant: "instructions addressed to an AI",
  role_or_system_claim: "a fake system or role message",
  tool_request: "a request to call a tool",
  exfiltration_url: "a link built to carry data out",
  encoded_payload: "an encoded payload",
  canary_like: "a marker token",
};

/** The warning on a citation whose code contains instruction-like text. */
export function flagWarning(flags: readonly string[]): string {
  const found = flags.map((flag) => FLAG_TEXT[flag] ?? flag).join(", ");
  return `This code contains text that looks like it was written to steer an AI (${found}). It was treated as data, not followed.`;
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`;
}

function Metrics({ answer }: { answer: AnswerView }) {
  const { metrics } = answer;
  return (
    <p className="text-[11px] text-fg-faint" data-testid="answer-metrics">
      {metrics.citations_total > 0
        ? `${metrics.citations_valid} of ${metrics.citations_total} citations valid before filtering (${percent(metrics.prefilter_citation_validity)})`
        : "No citations checked"}
      {metrics.claims_dropped > 0 ? ` · ${metrics.claims_dropped} claims dropped` : ""}
      {metrics.evidence_flagged > 0
        ? ` · ${metrics.evidence_flagged} of ${metrics.evidence_count} code excerpts flagged as instruction-like`
        : ""}
      {` · $${answer.cost_usd.toFixed(4)}`}
    </p>
  );
}

export interface AnswerBodyProps {
  answer: AnswerView;
  /** The citation whose code is open. */
  cited?: Citation | null;
  onCite: (citation: Citation) => void;
  /** The region's accessible name. */
  label?: string;
  /** Shown after the metrics (for example the trace). */
  children?: ReactNode;
}

export function AnswerBody({
  answer,
  cited = null,
  onCite,
  label = "Answer",
  children,
}: AnswerBodyProps) {
  return (
    <section aria-label={label} className="space-y-3">
      {answer.refused && answer.refusal_kind ? (
        <div className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm">
          <p className="font-medium text-fg">{REFUSAL_TITLE[answer.refusal_kind]}</p>
          {answer.refusal_reason ? (
            <p className="mt-0.5 text-fg-muted">{answer.refusal_reason}</p>
          ) : null}
        </div>
      ) : null}
      <Markdown>{answer.answer_markdown}</Markdown>
      {answer.claims.length > 0 ? (
        <ol aria-label="Claims" className="space-y-2">
          {answer.claims.map((claim) => (
            <li key={claim.id} className="rounded-lg border border-line px-3 py-2">
              <p className="text-sm text-fg">
                <span className="mr-1.5 font-mono text-[11px] text-fg-faint">[{claim.id}]</span>
                {claim.text}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {answer.citations
                  .filter((citation) => citation.claim_id === claim.id)
                  .map((citation) => {
                    const active =
                      cited?.claim_id === citation.claim_id &&
                      cited.evidence_id === citation.evidence_id;
                    const flags = citation.flags ?? [];
                    const where = `${citation.ref.path}, lines ${citation.ref.start_line}–${citation.ref.end_line}`;
                    return (
                      <button
                        key={citation.evidence_id}
                        type="button"
                        aria-pressed={active}
                        data-flagged={flags.length > 0 ? "true" : undefined}
                        title={flags.length > 0 ? `${where}. ${flagWarning(flags)}` : where}
                        onClick={() => onCite(citation)}
                        className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
                          active
                            ? "border-focus bg-focus/10 text-fg"
                            : "border-line bg-surface-2 text-fg-muted hover:text-fg"
                        }`}
                      >
                        {flags.length > 0 ? (
                          <TriangleAlert
                            className="h-3 w-3 text-warn"
                            role="img"
                            aria-label={flagWarning(flags)}
                          />
                        ) : null}
                        {citationLabel(citation)}
                      </button>
                    );
                  })}
              </div>
            </li>
          ))}
        </ol>
      ) : null}
      <Metrics answer={answer} />
      <TraceToggle
        traceId={answer.trace_id}
        costUsd={answer.cost_usd}
        tokens={
          answer.usage.input_tokens + answer.usage.cache_read_tokens + answer.usage.output_tokens
        }
      />
      {children}
    </section>
  );
}
