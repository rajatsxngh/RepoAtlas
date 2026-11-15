import { describe, expect, it } from "vitest";
import { ApiClient } from "../client";
import { ApiError } from "../errors";
import { MockBackend } from "./backend";
import { createMockFetch } from "./fetch";

function setup() {
  let now = Date.parse("2026-11-08T12:00:00Z");
  const backend = new MockBackend({ now: () => now, queueMs: 100, buildMs: 1000 });
  const api = new ApiClient({
    baseUrl: "http://mock.repoatlas.invalid/api/v1",
    fetch: createMockFetch(backend, { latencyMs: 0 }),
    sleep: async () => {},
  });
  return { api, advance: (ms: number) => (now += ms) };
}

describe("mock build flow", () => {
  it("queues a build, walks the stages, then serves the snapshot", async () => {
    const { api, advance } = setup();
    const accepted = await api.buildRepo("https://github.com/repoatlas-fixtures/ts-small");
    expect(accepted).toMatchObject({ snapshot_id: 2, reused: false, status: "pending" });

    const queued = await api.getJob(accepted.job_id!);
    expect(queued).toMatchObject({ status: "queued", stage: null, progress: 0 });
    const early = await api.getSubgraph(2, {}).catch((error: unknown) => error);
    expect(early).toMatchObject({
      status: 409,
      code: "snapshot_not_ready",
      details: { snapshot_id: 2, status: "pending", job_status: "queued" },
    });

    const stages: string[] = [];
    let last = 0;
    for (let step = 0; step < 12; step += 1) {
      advance(100);
      const job = await api.getJob(accepted.job_id!);
      if (job.stage && stages.at(-1) !== job.stage) stages.push(job.stage);
      expect(job.progress).toBeGreaterThanOrEqual(last);
      last = job.progress;
    }
    expect(stages).toEqual(["fetch", "inventory", "parse", "graph", "store"]);
    expect(await api.getJob(accepted.job_id!)).toMatchObject({ status: "succeeded", progress: 1 });
    expect((await api.getSnapshot(2)).status).toBe("ready");
    expect((await api.getSubgraph(2, {})).nodes.length).toBeGreaterThan(0);

    const again = await api.buildRepo("https://github.com/repoatlas-fixtures/ts-small.git");
    expect(again).toMatchObject({ snapshot_id: 2, reused: true, status: "ready" });
  });

  it("serves fixtures as ready before any build", async () => {
    const { api } = setup();
    const snapshot = await api.getSnapshot(1);
    expect(snapshot).toMatchObject({
      status: "ready",
      repo: { owner: "repoatlas-fixtures", name: "py-small" },
      job: null,
    });
    expect(snapshot.stats).toMatchObject({ nodes: 40 });
  });

  it("fails the broken fixture's build", async () => {
    const { api, advance } = setup();
    const accepted = await api.buildRepo("https://github.com/someone/broken");
    advance(2000);
    const job = await api.getJob(accepted.job_id!);
    expect(job).toMatchObject({ status: "failed", stage: "parse" });
    expect(job.error).toContain("refuses to build");
    expect((await api.getSnapshot(accepted.snapshot_id)).status).toBe("failed");
  });

  it.each([
    ["https://gitlab.com/o/r", 422, "invalid_url"],
    ["https://github.com/o/private", 403, "repo_private"],
    ["https://github.com/o/too-large", 413, "repo_too_large"],
    ["https://github.com/o/empty", 422, "repo_empty"],
    ["https://github.com/o/missing", 404, "not_found"],
    ["https://github.com/o/github-down", 502, "upstream_unavailable"],
    ["https://github.com/o/rate-limited", 429, "rate_limited"],
  ])("answers %s with %i %s", async (url, status, code) => {
    const { api } = setup();
    const error = await api.buildRepo(url).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code });
    if (code === "rate_limited") expect(error).toMatchObject({ retryAfterS: 42 });
  });

  it("explains a stored impact run, and only on the snapshot it belongs to", async () => {
    const { api } = setup();
    const report = await api.analyzeImpact(1, {
      symbol_key: "py:src/shop/cart.py:Cart.add:method",
      side: "old",
    });
    const answer = await api.explainImpact(1, report.run_id);
    expect(answer.refused).toBe(false);
    expect(answer.claims.length).toBeGreaterThan(0);
    expect(answer.citations.map((c) => c.claim_id)).toEqual(answer.claims.map((c) => c.id));
    expect(answer.trace_id).not.toBe(report.trace_id);
    await expect(api.explainImpact(1, 999)).rejects.toMatchObject({
      status: 404,
      details: { resource: "impact_run" },
    });
    await expect(api.explainImpact(2, report.run_id)).rejects.toMatchObject({ status: 404 });
  });

  it("answers reads like the API", async () => {
    const { api } = setup();
    const detail = await api.getNode(1, "py:src/shop/cart.py:Cart:class");
    expect(detail.node.name).toBe("Cart");
    const callers = await api.getNeighbors(1, "py:src/shop/cart.py:Cart._discount:method", {
      direction: "in",
      kinds: ["calls"],
    });
    expect(callers.nodes.map((hop) => hop.node.name)).toEqual(["describe"]);
    const found = await api.search(1, { q: "cart", limit: 5 });
    expect(found.results.length).toBeGreaterThan(0);
    const code = await api.readCode(1, { path: "src/shop/cart.py", start: 1, end: 3 });
    expect(code.end).toBe(3);
    await expect(api.getSnapshot(99)).rejects.toMatchObject({
      status: 404,
      details: { resource: "snapshot" },
    });
    await expect(api.getNode(1, "py:nope.py:x:function")).rejects.toMatchObject({ status: 404 });
    await expect(
      api.getNeighbors(1, "dir:src", { kinds: ["bogus" as never] }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

describe("mock freshness", () => {
  it("serves a stale snapshot of py-small whose rebuild is the current one", async () => {
    const { api, advance } = setup();
    const stale = await api.getFreshness(6);
    expect(stale).toMatchObject({
      snapshot_commit: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
      remote_head: (await api.getSnapshot(1)).commit_sha,
      stale: true,
      commits_behind: 3,
      default_branch: "main",
    });
    expect((await api.getSnapshot(6)).repo.name).toBe("py-small");

    // Building the repository again builds its head, never the old commit.
    const accepted = await api.buildRepo("https://github.com/repoatlas-fixtures/py-small");
    expect(accepted).toMatchObject({ snapshot_id: 1, reused: false, status: "pending" });
    advance(5000);
    expect(await api.getJob(accepted.job_id!)).toMatchObject({ status: "succeeded" });
    expect(await api.getFreshness(1)).toMatchObject({ stale: false, commits_behind: 0 });
    expect((await api.getFreshness(6)).stale).toBe(true);
  });

  it("says every other snapshot is current, and 404s for one that doesn't exist", async () => {
    const { api } = setup();
    for (const id of [1, 2, 3, 4]) {
      const fresh = await api.getFreshness(id);
      expect(fresh).toMatchObject({ stale: false, commits_behind: 0 });
      expect(fresh.remote_head).toBe(fresh.snapshot_commit);
    }
    const missing = await api.getFreshness(99).catch((error: unknown) => error);
    expect(missing).toBeInstanceOf(ApiError);
    expect(missing).toMatchObject({ status: 404, code: "not_found" });
  });
});
