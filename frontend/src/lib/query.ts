/** TanStack Query setup: shared defaults and the query keys of every API read. */

import { QueryClient } from "@tanstack/react-query";
import { isApiError } from "@/lib/api/errors";
import type { SubgraphLevel } from "@/lib/api/types";

/** Retry transient failures (network, 5xx) twice; never retry what the server refused. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (!isApiError(error)) return false;
  return error.code === "network" || error.status >= 500;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(4000, 500 * 2 ** attempt),
        refetchOnWindowFocus: false,
        // A ready snapshot never changes, so its reads stay fresh.
        staleTime: Number.POSITIVE_INFINITY,
        gcTime: 30 * 60 * 1000,
      },
      mutations: { retry: false },
    },
  });
}

export const queryKeys = {
  job: (jobId: number) => ["job", jobId] as const,
  snapshot: (snapshotId: number) => ["snapshot", snapshotId] as const,
  subgraph: (snapshotId: number, parent: string, level: SubgraphLevel) =>
    ["subgraph", snapshotId, parent, level] as const,
  node: (snapshotId: number, key: string) => ["node", snapshotId, key] as const,
  neighbors: (
    snapshotId: number,
    key: string,
    direction: string,
    kinds: readonly string[],
    depth: number,
  ) => ["neighbors", snapshotId, key, direction, [...kinds].sort().join(","), depth] as const,
  code: (snapshotId: number, path: string, start: number, end: number | null) =>
    ["code", snapshotId, path, start, end] as const,
  search: (snapshotId: number, q: string, kinds: readonly string[]) =>
    ["search", snapshotId, q, [...kinds].sort().join(",")] as const,
  path: (snapshotId: number, from: string, to: string) => ["path", snapshotId, from, to] as const,
};
