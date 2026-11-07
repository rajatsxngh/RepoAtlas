"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Circle, CircleAlert, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { JobView } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { buildSteps, isActive, type StepState } from "./stages";

export interface BuildProgressProps {
  jobId: number;
  /** What is being built, for example `owner/name`. */
  title?: ReactNode;
  /** Called once when the job succeeds. */
  onSucceeded?: (job: JobView) => void;
  /** Shown as "Try again" when the build fails. */
  onRetry?: () => void;
  pollMs?: number;
}

function seconds(fromIso: string, now: number): number {
  return Math.max(0, Math.round((now - Date.parse(fromIso)) / 1000));
}

function StepIcon({ state }: { state: StepState }) {
  switch (state) {
    case "done":
      return (
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ok/15 text-ok">
          <Check className="h-3.5 w-3.5" strokeWidth={3} aria-hidden="true" />
        </span>
      );
    case "current":
      return (
        <span className="flex h-5 w-5 items-center justify-center text-focus">
          <Spinner className="h-4 w-4" />
        </span>
      );
    case "failed":
      return (
        <span className="flex h-5 w-5 items-center justify-center text-danger">
          <CircleAlert className="h-4 w-4" aria-hidden="true" />
        </span>
      );
    case "pending":
      return (
        <span className="flex h-5 w-5 items-center justify-center text-fg-faint">
          <Circle className="h-3 w-3" aria-hidden="true" />
        </span>
      );
  }
}

const STATE_TEXT: Record<StepState, string> = {
  done: "done",
  current: "in progress",
  pending: "waiting",
  failed: "failed",
};

export function BuildProgress({
  jobId,
  title,
  onSucceeded,
  onRetry,
  pollMs = 1000,
}: BuildProgressProps) {
  const api = useApi();
  const job = useQuery({
    queryKey: queryKeys.job(jobId),
    queryFn: ({ signal }) => api.getJob(jobId, signal),
    refetchInterval: (query) => (isActive(query.state.data) ? pollMs : false),
    staleTime: 0,
    retry: 3,
  });
  const [seen, setSeen] = useState<readonly string[]>([]);
  const reported = useRef(false);
  const [now, setNow] = useState(() => Date.now());
  const data = job.data;
  // Stages the app doesn't know yet stay listed once seen (state adjusted during render).
  if (data?.stage && !seen.includes(data.stage)) setSeen([...seen, data.stage]);

  useEffect(() => {
    if (data?.status === "succeeded" && !reported.current) {
      reported.current = true;
      onSucceeded?.(data);
    }
  }, [data, onSucceeded]);

  useEffect(() => {
    if (!isActive(data)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [data]);

  if (job.isError && !data) {
    const { title: heading, detail } = describeError(job.error);
    return (
      <section className="rounded-2xl border border-line bg-surface p-6 shadow-card" role="alert">
        <h2 className="text-base font-semibold text-fg">{heading}</h2>
        <p className="mt-1 text-sm text-fg-muted">{detail}</p>
      </section>
    );
  }

  const steps = data ? buildSteps(data, seen) : buildSteps({ status: "queued", stage: null }, []);
  const percent = Math.round((data?.progress ?? 0) * 100);
  const failed = data?.status === "failed";
  const queued = !data || data.status === "queued";
  const retryIn =
    data && queued && data.attempts > 0
      ? Math.max(0, Math.ceil((Date.parse(data.run_after) - now) / 1000))
      : 0;
  const elapsed = data
    ? seconds(
        data.started_at ?? data.created_at,
        data.finished_at ? Date.parse(data.finished_at) : now,
      )
    : 0;

  let status: string;
  if (!data) status = "Starting…";
  else if (failed) status = "The build failed";
  else if (data.status === "succeeded") status = "Done — opening the map";
  else if (queued) status = retryIn > 0 ? `Retrying in ${retryIn} s` : "Waiting for a worker";
  else status = `${percent}% · ${elapsed} s`;

  return (
    <section
      className="w-full rounded-2xl border border-line bg-surface p-6 shadow-card"
      aria-labelledby="build-progress-title"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 id="build-progress-title" className="text-base font-semibold text-fg">
            {failed ? "Couldn't build the map" : "Building the map"}
          </h2>
          {title ? (
            <p className="mt-0.5 truncate font-mono text-sm text-fg-muted">{title}</p>
          ) : null}
        </div>
        <span className="shrink-0 pt-0.5 text-sm text-fg-muted tabular-nums" aria-live="polite">
          {status}
        </span>
      </div>
      <div
        className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-surface-3"
        role="progressbar"
        aria-label="Build progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out ${failed ? "bg-danger" : "bg-focus"}`}
          style={{ width: `${Math.max(failed ? percent : 2, percent)}%` }}
        />
      </div>
      <ol className="mt-5 space-y-2.5" aria-label="Build stages">
        {steps.map((step) => (
          <li key={step.stage} className="flex items-center gap-3 text-sm">
            <StepIcon state={step.state} />
            <span
              className={
                step.state === "pending"
                  ? "text-fg-faint"
                  : step.state === "failed"
                    ? "font-medium text-danger"
                    : step.state === "current"
                      ? "font-medium text-fg"
                      : "text-fg-muted"
              }
            >
              {step.label}
            </span>
            <span className="sr-only">({STATE_TEXT[step.state]})</span>
          </li>
        ))}
      </ol>
      {data && data.attempts > 1 && !failed ? (
        <p className="mt-4 text-xs text-fg-muted">
          Attempt {data.attempts} of {data.max_attempts}
          {data.error ? <> — the last one failed: {data.error}</> : null}
        </p>
      ) : null}
      {failed ? (
        <div
          className="mt-5 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3"
          role="alert"
        >
          <p className="text-sm text-fg">{data?.error ?? "The build stopped with an error."}</p>
          {onRetry ? (
            <Button
              className="mt-3"
              size="sm"
              onClick={onRetry}
              icon={<RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />}
            >
              Try again
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
