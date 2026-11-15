"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, GitCommitHorizontal, RefreshCw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { isActive, stageLabel } from "@/components/progress/stages";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError } from "@/lib/api/errors";
import type { Freshness, SnapshotView } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface FreshnessBannerProps {
  snapshot: SnapshotView;
  /** How often a running rebuild is polled (milliseconds). */
  pollMs?: number;
}

type Phase =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "building"; snapshotId: number; jobId: number }
  | { kind: "opening" };

/** The API reuses its answer for five minutes; asking more often would only repeat it. */
const FRESH_FOR_MS = 5 * 60 * 1000;

/** "3 commits behind main", or "behind main" when the count isn't known. */
export function behindText(freshness: Pick<Freshness, "commits_behind">): string {
  const count = freshness.commits_behind;
  if (count === null || count < 1) return "behind";
  return count === 1 ? "1 commit behind" : `${count.toLocaleString("en-US")} commits behind`;
}

/**
 * Says when the map was built from a commit that is no longer the head of the repository's
 * default branch, and rebuilds it on request. The rebuild is a normal build request for the
 * repository: the API pins the new head and reuses everything that did not change. Shows
 * nothing while the map is current, and nothing if the repository can't be asked.
 */
export function FreshnessBanner({ snapshot, pollMs = 1000 }: FreshnessBannerProps) {
  const api = useApi();
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [dismissed, setDismissed] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const opened = useRef(false);

  const freshness = useQuery({
    queryKey: queryKeys.freshness(snapshot.id),
    queryFn: ({ signal }) => api.getFreshness(snapshot.id, signal),
    staleTime: FRESH_FOR_MS,
    retry: false,
  });

  const jobId = phase.kind === "building" ? phase.jobId : null;
  const job = useQuery({
    queryKey: queryKeys.job(jobId ?? -1),
    queryFn: ({ signal }) => api.getJob(jobId!, signal),
    enabled: jobId !== null,
    refetchInterval: (query) => (isActive(query.state.data) ? pollMs : false),
    staleTime: 0,
    retry: 3,
  });

  const open = useCallback(
    (snapshotId: number) => {
      if (opened.current) return;
      opened.current = true;
      setPhase({ kind: "opening" });
      router.push(`/s/${snapshotId}`);
    },
    [router],
  );

  useEffect(() => {
    if (phase.kind !== "building") return;
    if (job.data?.status === "succeeded") open(phase.snapshotId);
  }, [job.data, phase, open]);

  const rebuild = useCallback(async () => {
    setFailure(null);
    setPhase({ kind: "starting" });
    try {
      const url = `https://github.com/${snapshot.repo.owner}/${snapshot.repo.name}`;
      const accepted = await api.buildRepo(url);
      if (accepted.snapshot_id === snapshot.id) {
        // The branch moved back, or the answer was old: this map is the head after all.
        setPhase({ kind: "idle" });
        setDismissed(true);
      } else if (accepted.status === "ready" || accepted.job_id === null) {
        open(accepted.snapshot_id);
      } else {
        setPhase({ kind: "building", snapshotId: accepted.snapshot_id, jobId: accepted.job_id });
      }
    } catch (caught) {
      const { title, detail } = describeError(caught);
      setFailure(`${title}. ${detail}`);
      setPhase({ kind: "idle" });
    }
  }, [api, open, snapshot]);

  const data = freshness.data;
  if (!data?.stale || dismissed) return null;

  const buildFailed = phase.kind === "building" && job.data?.status === "failed";
  const problem = buildFailed ? (job.data?.error ?? "The build stopped with an error.") : failure;
  const working = phase.kind !== "idle" && !buildFailed;
  const percent = Math.round((job.data?.progress ?? 0) * 100);
  let progress = "Starting the rebuild…";
  if (phase.kind === "opening") progress = "Done — opening the new map";
  else if (phase.kind === "building" && job.data?.status === "running") {
    progress = `${stageLabel(job.data.stage ?? "")} · ${percent}%`;
  } else if (phase.kind === "building") progress = "Waiting for a worker";

  return (
    <section
      aria-label="Map freshness"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line bg-warn-soft px-4 py-2 text-sm text-fg"
    >
      {problem ? (
        <CircleAlert className="h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
      ) : (
        <GitCommitHorizontal className="h-4 w-4 shrink-0 text-warn" aria-hidden="true" />
      )}
      <p className="min-w-0">
        This map is {behindText(data)}{" "}
        <span className="rounded bg-surface px-1 py-0.5 font-mono text-xs">
          {data.default_branch}
        </span>
        <span className="text-fg-muted">
          {" "}
          (built from {data.snapshot_commit.slice(0, 7)}, now at {data.remote_head.slice(0, 7)}).
        </span>
      </p>
      {working ? (
        <span className="inline-flex items-center gap-2 text-fg-muted" role="status">
          <Spinner className="h-3.5 w-3.5" />
          {progress}
        </span>
      ) : (
        <>
          {problem ? (
            <span className="text-danger" role="alert">
              The rebuild failed: {problem}
            </span>
          ) : null}
          <Button
            size="sm"
            variant="primary"
            onClick={() => void rebuild()}
            icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            {problem ? "Try again" : "Rebuild"}
          </Button>
        </>
      )}
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => setDismissed(true)}
        className="ml-auto flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-muted hover:bg-surface hover:text-fg"
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    </section>
  );
}
