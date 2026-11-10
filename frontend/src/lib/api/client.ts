/**
 * Typed client for the RepoAtlas API (`/api/v1`). Every method returns the spec's model or throws
 * an `ApiError`. Reads that get 429 wait for `Retry-After` and try again (a few times, short waits
 * only); build requests never retry on their own.
 */

import { encodeKeySegment } from "@/lib/graph/keys";
import { ApiError, errorFromResponse } from "./errors";
import type {
  BuildAccepted,
  CodeQuery,
  CodeSlice,
  EdgeLinks,
  EdgeLinksQuery,
  GraphPath,
  JobView,
  LocateBody,
  LocateQuery,
  NeighborsQuery,
  Neighborhood,
  NodeDetail,
  Overview,
  PathQuery,
  SearchQuery,
  SearchResults,
  SnapshotView,
  SubgraphBody,
  SubgraphQuery,
} from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;
export type Sleep = (ms: number, signal?: AbortSignal) => Promise<void>;

export interface RetryPolicy {
  /** Retries after a 429 on a read. */
  maxRetries: number;
  /** Longest `Retry-After` (seconds) worth waiting for; longer waits surface the error. */
  maxWaitS: number;
}

export interface ApiClientOptions {
  /** API origin plus prefix, for example `http://localhost:8000/api/v1`. */
  baseUrl: string;
  fetch?: FetchLike;
  retry?: Partial<RetryPolicy>;
  sleep?: Sleep;
  /** Told before each wait on a 429, with the seconds to wait. */
  onRateLimited?: (waitS: number) => void;
}

type QueryValue = string | number | boolean | readonly (string | number)[] | null | undefined;
type QueryParams = Record<string, QueryValue>;

interface RequestOptions {
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
}

const DEFAULT_RETRY: RetryPolicy = { maxRetries: 3, maxWaitS: 20 };

export const abortableSleep: Sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });

export function buildQuery(params: QueryParams | undefined): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) search.append(name, String(item));
    } else {
      search.append(name, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  private readonly retry: RetryPolicy;
  private readonly sleep: Sleep;
  private readonly onRateLimited?: (waitS: number) => void;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
    this.retry = { ...DEFAULT_RETRY, ...options.retry };
    this.sleep = options.sleep ?? abortableSleep;
    this.onRateLimited = options.onRateLimited;
  }

  /** One request; reads retry after short 429 waits. */
  async request<T>(method: "GET" | "POST", path: string, options: RequestOptions = {}): Promise<T> {
    const url = `${this.baseUrl}${path}${buildQuery(options.query)}`;
    const init: RequestInit = {
      method,
      headers:
        options.body === undefined
          ? { Accept: "application/json" }
          : { Accept: "application/json", "Content-Type": "application/json" },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    };
    for (let attempt = 0; ; attempt += 1) {
      let response: Response;
      try {
        response = await this.fetchImpl(url, init);
      } catch (error) {
        if (isAbort(error) || options.signal?.aborted) throw error;
        throw new ApiError(
          0,
          "network",
          "The RepoAtlas API can't be reached. Check that it is running and try again.",
        );
      }
      if (response.ok) {
        try {
          return (await response.json()) as T;
        } catch (error) {
          if (isAbort(error)) throw error;
          throw new ApiError(
            response.status,
            "bad_response",
            "The API sent a response that isn't JSON.",
          );
        }
      }
      const error = await errorFromResponse(response);
      const wait = error.retryAfterS ?? 1;
      const retryable =
        method === "GET" &&
        response.status === 429 &&
        attempt < this.retry.maxRetries &&
        wait <= this.retry.maxWaitS;
      if (!retryable) throw error;
      this.onRateLimited?.(wait);
      const jitter = Math.floor(Math.random() * 250);
      await this.sleep(Math.max(1, wait) * 1000 + jitter, options.signal);
    }
  }

  private snapshotPath(snapshotId: number, rest = ""): string {
    return `/snapshots/${encodeURIComponent(String(snapshotId))}${rest}`;
  }

  buildRepo(url: string, signal?: AbortSignal): Promise<BuildAccepted> {
    return this.request("POST", "/repos", { body: { url }, signal });
  }

  getJob(jobId: number, signal?: AbortSignal): Promise<JobView> {
    return this.request("GET", `/jobs/${encodeURIComponent(String(jobId))}`, { signal });
  }

  getSnapshot(snapshotId: number, signal?: AbortSignal): Promise<SnapshotView> {
    return this.request("GET", this.snapshotPath(snapshotId), { signal });
  }

  getSubgraph(
    snapshotId: number,
    query: SubgraphQuery,
    signal?: AbortSignal,
  ): Promise<SubgraphBody> {
    return this.request("GET", this.snapshotPath(snapshotId, "/subgraph"), { query, signal });
  }

  getEdgeLinks(
    snapshotId: number,
    query: EdgeLinksQuery,
    signal?: AbortSignal,
  ): Promise<EdgeLinks> {
    return this.request("GET", this.snapshotPath(snapshotId, "/links"), { query, signal });
  }

  getOverview(snapshotId: number, signal?: AbortSignal): Promise<Overview> {
    return this.request("GET", this.snapshotPath(snapshotId, "/overview"), { signal });
  }

  getNode(snapshotId: number, key: string, signal?: AbortSignal): Promise<NodeDetail> {
    return this.request("GET", this.snapshotPath(snapshotId, `/nodes/${encodeKeySegment(key)}`), {
      signal,
    });
  }

  getNeighbors(
    snapshotId: number,
    key: string,
    query: NeighborsQuery,
    signal?: AbortSignal,
  ): Promise<Neighborhood> {
    return this.request(
      "GET",
      this.snapshotPath(snapshotId, `/nodes/${encodeKeySegment(key)}/neighbors`),
      { query, signal },
    );
  }

  locate(snapshotId: number, query: LocateQuery, signal?: AbortSignal): Promise<LocateBody> {
    return this.request("GET", this.snapshotPath(snapshotId, "/locate"), { query, signal });
  }

  search(snapshotId: number, query: SearchQuery, signal?: AbortSignal): Promise<SearchResults> {
    return this.request("GET", this.snapshotPath(snapshotId, "/search"), { query, signal });
  }

  findPath(snapshotId: number, query: PathQuery, signal?: AbortSignal): Promise<GraphPath> {
    return this.request("GET", this.snapshotPath(snapshotId, "/path"), { query, signal });
  }

  readCode(snapshotId: number, query: CodeQuery, signal?: AbortSignal): Promise<CodeSlice> {
    return this.request("GET", this.snapshotPath(snapshotId, "/code"), { query, signal });
  }
}
