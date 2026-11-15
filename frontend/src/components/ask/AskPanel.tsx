"use client";

/**
 * Ask a question about the snapshot (ANS-12). The answer arrives after its citations are
 * checked; while it is being made, the panel shows which step is running. The answer's
 * markdown goes through the sanitized renderer; claims and paths are repo or model text,
 * rendered as text. Each citation is a chip: clicking it opens the cited lines in the code
 * viewer and tells the workspace to highlight the cited symbol on the map.
 *
 * Three modes (AGT-6, MAS-6): "Quick" retrieves once and answers; "Agent" explores the code
 * with tools first, and its tool calls appear one by one while it works (`LiveSteps`);
 * "Multi-agent" plans branches that are explored side by side, shows each branch's steps under
 * its sub-question, and checks every claim against the code it cites before showing it.
 */

import { MessageSquareText, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { CodeViewer } from "@/components/code/CodeViewer";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import { LiveSteps } from "@/components/trace/TracePanel";
import type {
  AnswerView,
  AskMode,
  AskPlan,
  AskStep,
  AskStepEvent,
  Citation,
} from "@/lib/api/types";
import { AnswerBody, citationLabel } from "./AnswerBody";

export interface AskPanelProps {
  snapshotId: number;
  /** A citation was opened: highlight and show its symbol on the map. */
  onCite?: (citation: Citation) => void;
  onClose?: () => void;
  className?: string;
}

const STEP_TEXT: Record<AskStep, string> = {
  retrieving: "Finding the relevant code…",
  planning: "Planning what to look into…",
  exploring: "Exploring the code…",
  answering: "Writing the answer…",
  writing: "Writing the answer…",
  verifying: "Checking every claim against the code…",
  validating: "Checking every citation…",
};

const FIRST_STEP: Record<AskMode, AskStep> = {
  oneshot: "retrieving",
  agent: "exploring",
  multiagent: "planning",
};

const MODES: readonly { mode: AskMode; label: string; hint: string }[] = [
  { mode: "oneshot", label: "Quick", hint: "One search of the code, then an answer." },
  {
    mode: "agent",
    label: "Agent",
    hint: "Explores the code with tools first. Slower, for questions one search can't answer.",
  },
  {
    mode: "multiagent",
    label: "Multi-agent",
    hint: "Splits the question across explorers and checks every claim. Slowest, for questions with several parts.",
  },
];

/** Lines of context shown around a cited range. */
const CONTEXT_LINES = 3;

export { citationLabel };

export function AskPanel({ snapshotId, onCite, onClose, className = "" }: AskPanelProps) {
  const api = useApi();
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AskMode>("oneshot");
  // The tool calls of the agent answer being made or shown (empty for quick answers).
  const [steps, setSteps] = useState<AskStepEvent[]>([]);
  // The branches of the multi-agent answer being made or shown.
  const [plan, setPlan] = useState<AskPlan | null>(null);
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
      setSteps([]);
      setPlan(null);
      setStep(FIRST_STEP[mode]);
      try {
        const outcome = await api.ask(
          snapshotId,
          { question: text, mode },
          {
            signal: current.signal,
            onStatus: setStep,
            onStep: (event) => setSteps((known) => [...known, event]),
            onPlan: setPlan,
          },
        );
        setAnswer(outcome.answer);
      } catch (caught) {
        if (!current.signal.aborted) setError(caught);
      } finally {
        if (controller.current === current) setStep(null);
      }
    },
    [api, busy, mode, question, snapshotId],
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
          <div
            role="radiogroup"
            aria-label="Answer mode"
            className="flex flex-wrap items-center gap-1.5"
          >
            {MODES.map((option) => (
              <label
                key={option.mode}
                title={option.hint}
                className={`cursor-pointer rounded-md border px-2 py-1 text-xs whitespace-nowrap has-focus-visible:ring-2 has-focus-visible:ring-focus ${
                  mode === option.mode
                    ? "border-focus bg-focus/10 text-fg"
                    : "border-line bg-surface-2 text-fg-muted hover:text-fg"
                } ${busy ? "cursor-not-allowed opacity-60" : ""}`}
              >
                <input
                  type="radio"
                  name="ask-mode"
                  value={option.mode}
                  checked={mode === option.mode}
                  disabled={busy}
                  onChange={() => setMode(option.mode)}
                  className="sr-only"
                />
                {option.label}
              </label>
            ))}
            <span className="basis-full text-[11px] text-fg-faint" data-testid="ask-mode-hint">
              {MODES.find((option) => option.mode === mode)?.hint}
            </span>
          </div>
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

        {steps.length > 0 || plan ? (
          <LiveSteps steps={steps} plan={plan} branches={answer?.branches} running={busy} />
        ) : null}

        {failure ? (
          <div role="alert" className="rounded-lg border border-line px-3 py-2 text-sm">
            <p className="font-medium text-fg">{failure.title}</p>
            <p className="mt-0.5 text-fg-muted">{failure.detail}</p>
          </div>
        ) : null}

        {answer ? <AnswerBody answer={answer} cited={cited} onCite={open} /> : null}

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
