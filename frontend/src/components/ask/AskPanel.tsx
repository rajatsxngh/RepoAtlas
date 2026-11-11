"use client";

/**
 * Ask a question about the snapshot (ANS-12). The answer arrives after its citations are
 * checked; while it is being made, the panel shows which step is running. The answer's
 * markdown goes through the sanitized renderer; claims and paths are repo or model text,
 * rendered as text. Each citation is a chip: clicking it opens the cited lines in the code
 * viewer and tells the workspace to highlight the cited symbol on the map.
 */

import { MessageSquareText, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { CodeViewer } from "@/components/code/CodeViewer";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { AnswerView, AskStep, Citation, RefusalKind } from "@/lib/api/types";
import { Markdown } from "@/lib/markdown";

export interface AskPanelProps {
  snapshotId: number;
  /** A citation was opened: highlight and show its symbol on the map. */
  onCite?: (citation: Citation) => void;
  onClose?: () => void;
  className?: string;
}

const STEP_TEXT: Record<AskStep, string> = {
  retrieving: "Finding the relevant code…",
  answering: "Writing the answer…",
  validating: "Checking every citation…",
};

const REFUSAL_TITLE: Record<RefusalKind, string> = {
  evidence: "Not enough evidence",
  api: "The model declined",
  budget: "Over the cost budget",
};

/** Lines of context shown around a cited range. */
const CONTEXT_LINES = 3;

export function citationLabel(citation: Citation): string {
  const { path, start_line: start, end_line: end } = citation.ref;
  const name = path.split("/").at(-1) ?? path;
  return start === end ? `${name}:${start}` : `${name}:${start}–${end}`;
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
      {` · $${answer.cost_usd.toFixed(4)}`}
    </p>
  );
}

export function AskPanel({ snapshotId, onCite, onClose, className = "" }: AskPanelProps) {
  const api = useApi();
  const [question, setQuestion] = useState("");
  const [step, setStep] = useState<AskStep | null>(null);
  const [answer, setAnswer] = useState<AnswerView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [cited, setCited] = useState<Citation | null>(null);
  const controller = useRef<AbortController | null>(null);
  const busy = step !== null;

  useEffect(() => () => controller.current?.abort(), []);

  const submit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const text = question.trim();
      if (!text || busy) return;
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      setAnswer(null);
      setError(null);
      setCited(null);
      setStep("retrieving");
      try {
        const outcome = await api.ask(
          snapshotId,
          { question: text, mode: "oneshot" },
          { signal: current.signal, onStatus: setStep },
        );
        setAnswer(outcome.answer);
      } catch (caught) {
        if (!current.signal.aborted) setError(caught);
      } finally {
        if (controller.current === current) setStep(null);
      }
    },
    [api, busy, question, snapshotId],
  );

  const open = useCallback(
    (citation: Citation) => {
      setCited(citation);
      onCite?.(citation);
    },
    [onCite],
  );

  const failure = error ? describeError(error) : null;

  return (
    <aside
      aria-label="Ask"
      className={`flex h-full flex-col overflow-hidden border-l border-line bg-surface ${className}`}
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <MessageSquareText className="h-4 w-4 text-fg-muted" aria-hidden="true" />
        <h2 className="flex-1 text-sm font-semibold text-fg">Ask about this code</h2>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close questions"
            icon={<X className="h-4 w-4" aria-hidden="true" />}
          />
        ) : null}
      </header>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <form onSubmit={(event) => void submit(event)} className="space-y-2">
          <label htmlFor="ask-question" className="block text-xs font-medium text-fg-muted">
            Question
          </label>
          <textarea
            id="ask-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.currentTarget.form?.requestSubmit();
              }
            }}
            rows={3}
            maxLength={2000}
            placeholder="How does checkout compute the total?"
            className="w-full resize-y rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-fg placeholder:text-fg-faint focus:ring-2 focus:ring-focus focus:outline-none"
          />
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !question.trim()}
              icon={busy ? <Spinner className="h-3.5 w-3.5" /> : null}
            >
              Ask
            </Button>
            <span className="text-[11px] text-fg-faint">
              Answers cite the code; claims that can&apos;t be verified are left out.
            </span>
          </div>
        </form>

        {step ? (
          <p role="status" className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> {STEP_TEXT[step]}
          </p>
        ) : null}

        {failure ? (
          <div role="alert" className="rounded-lg border border-line px-3 py-2 text-sm">
            <p className="font-medium text-fg">{failure.title}</p>
            <p className="mt-0.5 text-fg-muted">{failure.detail}</p>
          </div>
        ) : null}

        {answer ? (
          <section aria-label="Answer" className="space-y-3">
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
                      <span className="mr-1.5 font-mono text-[11px] text-fg-faint">
                        [{claim.id}]
                      </span>
                      {claim.text}
                    </p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {answer.citations
                        .filter((citation) => citation.claim_id === claim.id)
                        .map((citation) => {
                          const active =
                            cited?.claim_id === citation.claim_id &&
                            cited.evidence_id === citation.evidence_id;
                          return (
                            <button
                              key={citation.evidence_id}
                              type="button"
                              aria-pressed={active}
                              title={`${citation.ref.path}, lines ${citation.ref.start_line}–${citation.ref.end_line}`}
                              onClick={() => open(citation)}
                              className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
                                active
                                  ? "border-focus bg-focus/10 text-fg"
                                  : "border-line bg-surface-2 text-fg-muted hover:text-fg"
                              }`}
                            >
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
          </section>
        ) : null}

        {cited ? (
          <CodeViewer
            key={`${cited.claim_id}|${cited.evidence_id}`}
            snapshotId={snapshotId}
            path={cited.ref.path}
            startLine={Math.max(1, cited.ref.start_line - CONTEXT_LINES)}
            endLine={cited.ref.end_line + CONTEXT_LINES}
            highlightRanges={[{ start: cited.ref.start_line, end: cited.ref.end_line }]}
            className="max-h-80"
          />
        ) : null}
      </div>
    </aside>
  );
}

/** The map layer for a cited symbol (its file when the citation names no symbol). */
export const CITATION_LAYER_ID = "citation";

export function citationLayer(citation: Citation) {
  const key = citation.ref.symbol_key ?? `file:${citation.ref.path}`;
  return {
    id: CITATION_LAYER_ID,
    nodeKeys: [key],
    style: "primary" as const,
    label: `Cited: ${citationLabel(citation)}`,
  };
}
