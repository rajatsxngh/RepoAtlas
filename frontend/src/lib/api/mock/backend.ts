/**
 * Mock mode's API: the same routes, models, and error model as `/api/v1`, served from fixtures
 * in memory. Builds are simulated: `POST /repos` starts a job that walks the real stage names
 * over a couple of seconds, and reads answer 409 until it finishes.
 */

import { checkRepoUrl } from "@/lib/repoUrl";
import { EDGE_KINDS, NODE_KINDS } from "../types";
import type {
  BuildAccepted,
  Direction,
  EdgeKind,
  JobView,
  NodeKind,
  SnapshotView,
  SubgraphLevel,
} from "../types";
import {
  FIXTURE_OWNER,
  MOCK_FIXTURES,
  fixtureById,
  fixtureByName,
  type MockFixture,
} from "./fixtures";
import { MockApiError, MockGraph } from "./graph";

export interface MockResponse {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
}

export interface MockBackendOptions {
  now?: () => number;
  /** How long a simulated build waits in the queue, then runs (milliseconds). */
  queueMs?: number;
  buildMs?: number;
}

/** The initial pipeline's stages and weights (M04's stage registry). */
export const MOCK_STAGES: readonly (readonly [string, number])[] = [
  ["fetch", 2],
  ["inventory", 1],
  ["parse", 4],
  ["graph", 3],
  ["store", 1],
];
const TOTAL_WEIGHT = MOCK_STAGES.reduce((sum, [, weight]) => sum + weight, 0);

/** Repo names that answer like GitHub's failure cases. */
const FAILURES: Record<string, () => MockApiError> = {
  private: () =>
    new MockApiError(
      403,
      "repo_private",
      `${FIXTURE_OWNER}/private is private or needs authentication; only public repositories can be mapped.`,
    ),
  "too-large": () =>
    new MockApiError(
      413,
      "repo_too_large",
      "This repository is about 2400 MB on GitHub; the limit is 488 MB.",
    ),
  empty: () => new MockApiError(422, "repo_empty", "The repository has no commits."),
  missing: () =>
    new MockApiError(404, "not_found", "GitHub has no repository at that URL.", {
      resource: "repo",
    }),
  "github-down": () =>
    new MockApiError(502, "upstream_unavailable", "GitHub did not answer (HTTP 503)."),
  "rate-limited": () =>
    new MockApiError(
      429,
      "rate_limited",
      "Too many build requests: the limit is 5 per hour. Try again in 42 s.",
      { group: "build", limit: 5, period_s: 3600, retry_after_s: 42 },
      { "Retry-After": "42" },
    ),
};

interface MockJob {
  id: number;
  snapshotId: number;
  createdAt: number;
  fails: boolean;
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function intParam(value: string | null, name: string, fallback?: number): number {
  if (value === null || value === "") {
    if (fallback === undefined)
      throw new MockApiError(422, "invalid_request", `${name} is required.`);
    return fallback;
  }
  if (!/^-?\d+$/.test(value)) {
    throw new MockApiError(422, "invalid_request", `${name} must be an integer.`);
  }
  return Number.parseInt(value, 10);
}

function listParam<T extends string>(
  values: string[],
  allowed: readonly T[],
  name: string,
): T[] | null {
  if (!values.length) return null;
  for (const value of values) {
    if (!(allowed as readonly string[]).includes(value)) {
      throw new MockApiError(422, "invalid_request", `Unknown ${name}: ${value}.`);
    }
  }
  return values as T[];
}

export class MockBackend {
  private readonly now: () => number;
  private readonly queueMs: number;
  private readonly buildMs: number;
  private readonly graphs = new Map<number, Promise<MockGraph>>();
  private readonly jobs = new Map<number, MockJob>();
  private readonly latest = new Map<number, number>();
  private readonly bootedAt: number;
  private nextJobId = 1;

  constructor(options: MockBackendOptions = {}) {
    this.now = options.now ?? (() => Date.now());
    this.queueMs = options.queueMs ?? 400;
    this.buildMs = options.buildMs ?? 2400;
    this.bootedAt = this.now();
  }

  graph(snapshotId: number): Promise<MockGraph> {
    const fixture = fixtureById(snapshotId);
    if (!fixture) {
      return Promise.reject(
        new MockApiError(404, "not_found", `There is no snapshot ${snapshotId}.`, {
          resource: "snapshot",
        }),
      );
    }
    let graph = this.graphs.get(snapshotId);
    if (!graph) {
      graph = fixture.load().then((data) => new MockGraph(data));
      this.graphs.set(snapshotId, graph);
    }
    return graph;
  }

  private jobView(job: MockJob): JobView {
    const elapsed = this.now() - job.createdAt;
    const run = elapsed - this.queueMs;
    const base = {
      id: job.id,
      kind: "build",
      snapshot_id: job.snapshotId,
      attempts: run >= 0 ? 1 : 0,
      max_attempts: 3,
      run_after: iso(job.createdAt),
      created_at: iso(job.createdAt),
    };
    if (run < 0) {
      return {
        ...base,
        status: "queued",
        stage: null,
        progress: 0,
        error: null,
        updated_at: iso(job.createdAt),
        started_at: null,
        finished_at: null,
      };
    }
    const fraction = Math.min(1, run / this.buildMs);
    const failAt = job.fails ? 0.55 : 1;
    let done = 0;
    let stage = MOCK_STAGES[0]![0];
    for (const [name, weight] of MOCK_STAGES) {
      stage = name;
      if (Math.min(fraction, failAt) * TOTAL_WEIGHT < done + weight) break;
      done += weight;
    }
    const started = iso(job.createdAt + this.queueMs);
    if (fraction >= failAt) {
      const finished = iso(job.createdAt + this.queueMs + this.buildMs * failAt);
      return job.fails
        ? {
            ...base,
            status: "failed",
            stage,
            progress: Math.round(failAt * 1000) / 1000,
            error: "ParseError: the fixture refuses to build (mock mode).",
            updated_at: finished,
            started_at: started,
            finished_at: finished,
          }
        : {
            ...base,
            status: "succeeded",
            stage: "store",
            progress: 1,
            error: null,
            updated_at: finished,
            started_at: started,
            finished_at: finished,
          };
    }
    return {
      ...base,
      status: "running",
      stage,
      progress: Math.round(fraction * 1000) / 1000,
      error: null,
      updated_at: iso(this.now()),
      started_at: started,
      finished_at: null,
    };
  }

  private latestJob(snapshotId: number): JobView | null {
    const id = this.latest.get(snapshotId);
    const job = id === undefined ? undefined : this.jobs.get(id);
    return job ? this.jobView(job) : null;
  }

  private status(fixture: MockFixture): {
    status: SnapshotView["status"];
    job: JobView | null;
    error: string | null;
  } {
    const job = this.latestJob(fixture.id);
    if (!job) {
      return fixture.failsToBuild
        ? {
            status: "failed",
            job: null,
            error: "ParseError: the fixture refuses to build (mock mode).",
          }
        : { status: "ready", job: null, error: null };
    }
    switch (job.status) {
      case "queued":
        return { status: "pending", job, error: null };
      case "running":
        return { status: "building", job, error: null };
      case "succeeded":
        return { status: "ready", job, error: null };
      case "failed":
        return { status: "failed", job, error: job.error };
    }
  }

  buildRepo(body: unknown): BuildAccepted {
    const url = body && typeof body === "object" ? (body as { url?: unknown }).url : undefined;
    if (typeof url !== "string") {
      throw new MockApiError(422, "invalid_request", "body.url: Field required");
    }
    const checked = checkRepoUrl(url);
    if (!checked.ok) throw new MockApiError(422, "invalid_url", checked.message);
    const failure = FAILURES[checked.name.toLowerCase()];
    if (failure) throw failure();
    const fixture = fixtureByName(checked.name) ?? MOCK_FIXTURES[0]!;
    const current = this.status(fixture);
    if (
      current.job &&
      (current.status === "pending" || current.status === "building" || current.status === "ready")
    ) {
      return {
        snapshot_id: fixture.id,
        job_id: current.job.id,
        reused: true,
        status: current.status,
      };
    }
    const job: MockJob = {
      id: this.nextJobId++,
      snapshotId: fixture.id,
      createdAt: this.now(),
      fails: Boolean(fixture.failsToBuild),
    };
    this.jobs.set(job.id, job);
    this.latest.set(fixture.id, job.id);
    return { snapshot_id: fixture.id, job_id: job.id, reused: false, status: "pending" };
  }

  getJob(jobId: number): JobView {
    const job = this.jobs.get(jobId);
    if (!job)
      throw new MockApiError(404, "not_found", `There is no job ${jobId}.`, { resource: "job" });
    return this.jobView(job);
  }

  async getSnapshot(snapshotId: number): Promise<SnapshotView> {
    const fixture = fixtureById(snapshotId);
    if (!fixture) {
      throw new MockApiError(404, "not_found", `There is no snapshot ${snapshotId}.`, {
        resource: "snapshot",
      });
    }
    const { status, job, error } = this.status(fixture);
    const stats: Record<string, unknown> = {};
    if (status === "ready") {
      const graph = await this.graph(snapshotId);
      const byKind: Record<string, number> = {};
      for (const node of graph.nodes.values()) byKind[node.kind] = (byKind[node.kind] ?? 0) + 1;
      stats.nodes = graph.nodeCount;
      stats.nodes_by_kind = byKind;
    }
    return {
      id: fixture.id,
      repo: {
        host: "github.com",
        owner: fixture.owner,
        name: fixture.name,
        default_branch: "main",
      },
      commit_sha: fixture.commit,
      config_fingerprint: "mock".padEnd(64, "0"),
      status,
      stats,
      embedding_model: null,
      embedding_dim: null,
      error,
      created_at: iso(this.bootedAt),
      updated_at: iso(job ? Date.parse(job.updated_at) : this.bootedAt),
      job,
    };
  }

  private async ready(snapshotId: number): Promise<MockGraph> {
    const fixture = fixtureById(snapshotId);
    if (!fixture) {
      throw new MockApiError(404, "not_found", `There is no snapshot ${snapshotId}.`, {
        resource: "snapshot",
      });
    }
    const { status, job, error } = this.status(fixture);
    if (status !== "ready") {
      throw new MockApiError(
        409,
        "snapshot_not_ready",
        `Snapshot ${snapshotId} is ${status}; it can be read once it is ready.`,
        {
          snapshot_id: snapshotId,
          status,
          job_id: job?.id ?? null,
          job_status: job?.status ?? null,
          error,
        },
      );
    }
    return this.graph(snapshotId);
  }

  /** Answers one request (method, URL under `/api/v1`, parsed JSON body). */
  async handle(method: string, url: URL, body: unknown): Promise<MockResponse> {
    try {
      return await this.route(method, url, body);
    } catch (error) {
      if (error instanceof MockApiError) {
        return {
          status: error.status,
          body: { error: { code: error.code, message: error.message, details: error.details } },
          headers: error.headers,
        };
      }
      return {
        status: 500,
        body: {
          error: {
            code: "internal",
            message: "The mock API failed.",
            details: { request_id: "mock" },
          },
        },
      };
    }
  }

  private async route(method: string, url: URL, body: unknown): Promise<MockResponse> {
    const prefix = "/api/v1";
    if (!url.pathname.startsWith(prefix)) throw new MockApiError(404, "not_found", "Not Found");
    const path = url.pathname.slice(prefix.length);
    const query = url.searchParams;
    if (method === "POST" && path === "/repos") return { status: 202, body: this.buildRepo(body) };
    if (method !== "GET") throw new MockApiError(405, "invalid_request", "Method Not Allowed");
    if (path === "/health") return { status: 200, body: { status: "ok", version: "mock" } };
    if (path === "/ready") {
      return {
        status: 200,
        body: { status: "ready", database: "ok", revision: "mock", expected_revision: "mock" },
      };
    }
    const job = /^\/jobs\/(\d+)$/.exec(path);
    if (job) return { status: 200, body: this.getJob(Number(job[1])) };
    const snapshot = /^\/snapshots\/(\d+)(\/.*)?$/.exec(path);
    if (!snapshot) throw new MockApiError(404, "not_found", "Not Found");
    const snapshotId = Number(snapshot[1]);
    const rest = snapshot[2] ?? "";
    if (rest === "") return { status: 200, body: await this.getSnapshot(snapshotId) };
    const graph = await this.ready(snapshotId);
    if (rest === "/subgraph") {
      const level = (query.get("level") ?? "dir") as SubgraphLevel;
      if (!["dir", "file", "symbol"].includes(level)) {
        throw new MockApiError(
          422,
          "invalid_request",
          "The level must be one of dir, file, symbol.",
        );
      }
      return { status: 200, body: graph.subgraph(query.get("parent") ?? "dir:.", level) };
    }
    if (rest === "/links") {
      const kind = query.get("kind") as EdgeKind | null;
      const src = query.get("from");
      const dst = query.get("to");
      if (!kind || !EDGE_KINDS.includes(kind) || !src || !dst) {
        throw new MockApiError(422, "invalid_request", "from, to, and a valid kind are required.");
      }
      return {
        status: 200,
        body: graph.edgeLinks(src, dst, kind, intParam(query.get("limit"), "limit", 50)),
      };
    }
    if (rest === "/overview") return { status: 200, body: graph.overview() };
    const node = /^\/nodes\/([^/]+)(\/neighbors)?$/.exec(rest);
    if (node) {
      const key = decodeURIComponent(node[1]!);
      if (!node[2]) return { status: 200, body: graph.nodeDetail(key) };
      const direction = (query.get("direction") ?? "out") as Direction;
      if (!["in", "out", "both"].includes(direction)) {
        throw new MockApiError(422, "invalid_request", "direction must be in, out, or both.");
      }
      const kinds = listParam<EdgeKind>(query.getAll("kinds"), EDGE_KINDS, "edge kind");
      return {
        status: 200,
        body: graph.neighbors(key, direction, kinds, intParam(query.get("depth"), "depth", 1)),
      };
    }
    if (rest === "/locate") {
      return {
        status: 200,
        body: graph.locate(query.get("path") ?? "", intParam(query.get("line"), "line")),
      };
    }
    if (rest === "/search") {
      const q = query.get("q");
      if (q === null) throw new MockApiError(422, "invalid_request", "q is required.");
      const kinds = listParam<NodeKind>(query.getAll("kinds"), NODE_KINDS, "node kind");
      return {
        status: 200,
        body: {
          query: q,
          results: graph.search(q, kinds, intParam(query.get("limit"), "limit", 20)),
        },
      };
    }
    if (rest === "/path") {
      const from = query.get("from");
      const to = query.get("to");
      if (!from || !to) throw new MockApiError(422, "invalid_request", "from and to are required.");
      const kinds = listParam<EdgeKind>(query.getAll("kinds"), EDGE_KINDS, "edge kind");
      return { status: 200, body: graph.path(from, to, kinds) };
    }
    if (rest === "/code") {
      const path = query.get("path");
      if (!path) throw new MockApiError(422, "invalid_request", "path is required.");
      const end = query.get("end");
      return {
        status: 200,
        body: graph.code(
          path,
          intParam(query.get("start"), "start", 1),
          end ? intParam(end, "end") : null,
        ),
      };
    }
    throw new MockApiError(404, "not_found", "Not Found");
  }
}
