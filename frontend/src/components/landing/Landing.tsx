"use client";

import { CircleAlert, Clock, Lock, Scale } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BuildProgress } from "@/components/progress/BuildProgress";
import { Button } from "@/components/ui/Button";
import { useApi } from "@/lib/api/context";
import { describeError, isApiError } from "@/lib/api/errors";
import { config } from "@/lib/config";
import { RepoForm } from "./RepoForm";

type Phase =
  | { kind: "idle" }
  | { kind: "submitting"; url: string }
  | { kind: "building"; url: string; repo: string; snapshotId: number; jobId: number }
  | { kind: "opening"; snapshotId: number };

export const REAL_EXAMPLES = ["pallets/flask", "colinhacks/zod", "fastapi/fastapi"] as const;
export const MOCK_EXAMPLES = [
  "repoatlas-fixtures/py-small",
  "repoatlas-fixtures/ts-small",
  "repoatlas-fixtures/synthetic",
] as const;

function repoName(url: string): string {
  return url.replace(/^https:\/\/github\.com\//, "");
}

function ErrorIcon({ code }: { code: string }) {
  const className = "mt-0.5 h-4 w-4 shrink-0";
  if (code === "repo_private") return <Lock className={className} aria-hidden="true" />;
  if (code === "repo_too_large") return <Scale className={className} aria-hidden="true" />;
  if (code === "rate_limited") return <Clock className={className} aria-hidden="true" />;
  return <CircleAlert className={className} aria-hidden="true" />;
}

export interface LandingProps {
  pollMs?: number;
}

export function Landing({ pollMs }: LandingProps) {
  const api = useApi();
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [error, setError] = useState<unknown>(null);
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const waitS = retryAt ? Math.max(0, Math.ceil((retryAt - now) / 1000)) : 0;

  useEffect(() => {
    if (!retryAt) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= retryAt) setRetryAt(null);
    }, 250);
    return () => clearInterval(timer);
  }, [retryAt]);

  const open = useCallback(
    (snapshotId: number) => {
      setPhase({ kind: "opening", snapshotId });
      router.push(`/s/${snapshotId}`);
    },
    [router],
  );

  const submit = useCallback(
    async (url: string) => {
      setError(null);
      setPhase({ kind: "submitting", url });
      try {
        const accepted = await api.buildRepo(url);
        if (accepted.status === "ready" || accepted.job_id === null) {
          open(accepted.snapshot_id);
        } else {
          setPhase({
            kind: "building",
            url,
            repo: repoName(url),
            snapshotId: accepted.snapshot_id,
            jobId: accepted.job_id,
          });
        }
      } catch (caught) {
        setError(caught);
        setPhase({ kind: "idle" });
        if (isApiError(caught) && caught.code === "rate_limited" && caught.retryAfterS) {
          setNow(Date.now());
          setRetryAt(Date.now() + caught.retryAfterS * 1000);
        }
      }
    },
    [api, open],
  );

  const described = error ? describeError(error) : null;
  const errorCode = isApiError(error) ? error.code : "internal";

  return (
    <div className="w-full">
      {phase.kind === "building" ? (
        <div className="space-y-3">
          <BuildProgress
            jobId={phase.jobId}
            title={phase.repo}
            pollMs={pollMs}
            onSucceeded={() => open(phase.snapshotId)}
            onRetry={() => void submit(phase.url)}
          />
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => setPhase({ kind: "idle" })}>
              Map a different repository
            </Button>
          </div>
        </div>
      ) : (
        <RepoForm
          onSubmit={(url) => void submit(url)}
          busy={phase.kind === "submitting" || phase.kind === "opening"}
          waitS={waitS}
          examples={config.mock ? MOCK_EXAMPLES : REAL_EXAMPLES}
        />
      )}
      {described && phase.kind !== "building" ? (
        <div
          role="alert"
          className="mt-4 flex gap-3 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-fg"
        >
          <ErrorIcon code={errorCode} />
          <div>
            <p className="font-medium">{described.title}</p>
            <p className="mt-0.5 text-fg-muted">
              {errorCode === "rate_limited" && waitS > 0
                ? `You can try again in ${waitS} s.`
                : described.detail}
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
