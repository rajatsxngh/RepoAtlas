"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { BuildProgress } from "@/components/progress/BuildProgress";
import { Button } from "@/components/ui/Button";
import { SiteHeader } from "@/components/ui/SiteHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useApi } from "@/lib/api/context";
import { describeError, isApiError } from "@/lib/api/errors";
import type { SnapshotView } from "@/lib/api/types";
import { queryKeys } from "@/lib/query";

export interface SnapshotGateProps {
  snapshotId: number;
  /** Renders the workspace once the snapshot is ready. */
  children: (snapshot: SnapshotView) => ReactNode;
  pollMs?: number;
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center px-5 pb-24">
        {children}
      </main>
    </div>
  );
}

export function SnapshotGate({ snapshotId, children, pollMs }: SnapshotGateProps) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [rebuildJob, setRebuildJob] = useState<number | null>(null);
  const snapshot = useQuery({
    queryKey: queryKeys.snapshot(snapshotId),
    queryFn: ({ signal }) => api.getSnapshot(snapshotId, signal),
    staleTime: (query) => (query.state.data?.status === "ready" ? Number.POSITIVE_INFINITY : 0),
  });
  const rebuild = useMutation({
    mutationFn: (view: SnapshotView) =>
      api.buildRepo(`https://github.com/${view.repo.owner}/${view.repo.name}`),
    onSuccess: (accepted) => {
      if (accepted.job_id !== null && accepted.status !== "ready") setRebuildJob(accepted.job_id);
      else void queryClient.invalidateQueries({ queryKey: queryKeys.snapshot(snapshotId) });
    },
  });

  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: queryKeys.snapshot(snapshotId) });

  if (snapshot.isPending) {
    return (
      <Centered>
        <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
          <Spinner /> Loading the map…
        </p>
      </Centered>
    );
  }

  if (snapshot.isError) {
    const missing = isApiError(snapshot.error) && snapshot.error.status === 404;
    const { title, detail } = describeError(snapshot.error);
    return (
      <Centered>
        <div role="alert" className="rounded-2xl border border-line bg-surface p-6 shadow-card">
          <h1 className="text-lg font-semibold text-fg">
            {missing ? "There's no map here" : title}
          </h1>
          <p className="mt-1 text-sm text-fg-muted">
            {missing ? `Snapshot ${snapshotId} doesn't exist. It may have been removed.` : detail}
          </p>
          <div className="mt-4 flex gap-2">
            <Link
              href="/"
              className="inline-flex h-9 items-center rounded-lg border border-line bg-surface px-3.5 text-sm font-medium text-fg hover:bg-surface-2"
            >
              Map a repository
            </Link>
            {missing ? null : (
              <Button
                onClick={() => void snapshot.refetch()}
                icon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}
              >
                Try again
              </Button>
            )}
          </div>
        </div>
      </Centered>
    );
  }

  const view = snapshot.data;
  const repo = `${view.repo.owner}/${view.repo.name}`;
  const jobId = rebuildJob ?? view.job?.id ?? null;

  if (view.status === "ready" && rebuildJob === null) return <>{children(view)}</>;

  if (
    (view.status === "pending" || view.status === "building" || rebuildJob !== null) &&
    jobId !== null
  ) {
    return (
      <Centered>
        <BuildProgress
          key={jobId}
          jobId={jobId}
          title={repo}
          pollMs={pollMs}
          onSucceeded={() => {
            setRebuildJob(null);
            refresh();
          }}
          onRetry={() => rebuild.mutate(view)}
        />
      </Centered>
    );
  }

  return (
    <Centered>
      <div role="alert" className="rounded-2xl border border-line bg-surface p-6 shadow-card">
        <div className="flex items-start gap-3">
          <CircleAlert className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden="true" />
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-fg">
              {view.status === "failed" ? "This map couldn't be built" : "This map isn't ready"}
            </h1>
            <p className="mt-1 text-sm text-fg-muted">
              <span className="font-mono">{repo}</span>
              {view.error ? <> — {view.error}</> : null}
            </p>
            {rebuild.isError ? (
              <p className="mt-2 text-sm text-danger">{describeError(rebuild.error).detail}</p>
            ) : null}
            <Button
              className="mt-4"
              variant="primary"
              onClick={() => rebuild.mutate(view)}
              disabled={rebuild.isPending}
              icon={
                rebuild.isPending ? (
                  <Spinner />
                ) : (
                  <RotateCcw className="h-4 w-4" aria-hidden="true" />
                )
              }
            >
              Build it again
            </Button>
          </div>
        </div>
      </div>
    </Centered>
  );
}
