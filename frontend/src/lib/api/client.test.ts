import { describe, expect, it, vi } from "vitest";
import { ApiClient, buildQuery, type FetchLike } from "./client";
import { ApiError, describeError, parseRetryAfter } from "./errors";

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function errorBody(code: string, message: string, details: Record<string, unknown> = {}) {
  return { error: { code, message, details } };
}

function client(fetch: FetchLike, sleep = vi.fn(async () => {})) {
  return {
    api: new ApiClient({ baseUrl: "http://api.test/api/v1/", fetch, sleep }),
    sleep,
  };
}

describe("ApiClient requests", () => {
  it("builds typed URLs: repeated kinds, one-segment keys, no empty params", async () => {
    const seen: string[] = [];
    const fetch: FetchLike = async (input) => {
      seen.push(input);
      return json(200, {});
    };
    const { api } = client(fetch);
    await api.getNeighbors(7, "py:src/a b.py:f:function", {
      direction: "in",
      kinds: ["calls", "imports"],
      depth: 2,
    });
    await api.getSubgraph(7, { parent: "dir:src", level: "dir" });
    await api.readCode(7, { path: "src/a.py", start: 3, end: null });
    await api.findPath(7, { from: "py:a.py:x:function", to: "py:b.py:y:function" });
    expect(seen).toEqual([
      "http://api.test/api/v1/snapshots/7/nodes/py%3Asrc%2Fa%20b.py%3Af%3Afunction/neighbors?direction=in&kinds=calls&kinds=imports&depth=2",
      "http://api.test/api/v1/snapshots/7/subgraph?parent=dir%3Asrc&level=dir",
      "http://api.test/api/v1/snapshots/7/code?path=src%2Fa.py&start=3",
      "http://api.test/api/v1/snapshots/7/path?from=py%3Aa.py%3Ax%3Afunction&to=py%3Ab.py%3Ay%3Afunction",
    ]);
  });

  it("posts build requests as JSON", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      json(202, { snapshot_id: 1, job_id: 2, reused: false, status: "pending" }),
    );
    const { api } = client(fetch);
    await expect(api.buildRepo("https://github.com/o/r")).resolves.toMatchObject({ job_id: 2 });
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("http://api.test/api/v1/repos");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"url":"https://github.com/o/r"}');
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
  });

  it("buildQuery skips null and undefined", () => {
    expect(buildQuery({ a: 1, b: undefined, c: null, d: ["x", "y"], e: false })).toBe(
      "?a=1&d=x&d=y&e=false",
    );
    expect(buildQuery({})).toBe("");
  });
});

describe("ApiClient errors", () => {
  it("maps the API's error model", async () => {
    const { api } = client(async () =>
      json(409, errorBody("snapshot_not_ready", "Snapshot 3 is building.", { job_id: 9 }), {
        "X-Request-ID": "req-1",
      }),
    );
    const error = await api.getSubgraph(3, {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "snapshot_not_ready",
      message: "Snapshot 3 is building.",
      details: { job_id: 9 },
      requestId: "req-1",
    });
  });

  it("maps bodies that are not the error model", async () => {
    const { api } = client(async () => new Response("<html>bad gateway</html>", { status: 502 }));
    await expect(api.getSnapshot(1)).rejects.toMatchObject({ status: 502, code: "internal" });
    const { api: other } = client(async () => json(418, { error: { code: "teapot" } }));
    await expect(other.getSnapshot(1)).rejects.toMatchObject({ code: "bad_response" });
  });

  it("reports an unreachable API as a network error", async () => {
    const { api } = client(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(api.getSnapshot(1)).rejects.toMatchObject({ status: 0, code: "network" });
  });

  it("lets aborts through untouched", async () => {
    const controller = new AbortController();
    const { api } = client(async () => {
      controller.abort();
      throw new DOMException("Aborted", "AbortError");
    });
    await expect(api.getSnapshot(1, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
  });

  it("rejects a 200 that is not JSON", async () => {
    const { api } = client(async () => new Response("not json", { status: 200 }));
    await expect(api.getSnapshot(1)).rejects.toMatchObject({ code: "bad_response" });
  });
});

describe("429 backoff", () => {
  const limited = () =>
    json(429, errorBody("rate_limited", "Too many requests.", { group: "read" }), {
      "Retry-After": "2",
    });

  it("waits Retry-After on reads, tells listeners, then retries", async () => {
    const answers = [limited(), limited(), json(200, { ok: true })];
    const fetch = vi.fn<FetchLike>(async () => answers.shift()!);
    const sleep = vi.fn(async () => {});
    const onRateLimited = vi.fn();
    const api = new ApiClient({ baseUrl: "http://api.test/api/v1", fetch, sleep, onRateLimited });
    await expect(api.getSnapshot(1)).resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(onRateLimited).toHaveBeenCalledWith(2);
    expect(sleep).toHaveBeenCalledTimes(2);
    const [ms] = sleep.mock.calls[0] as unknown as [number];
    expect(ms).toBeGreaterThanOrEqual(2000);
    expect(ms).toBeLessThan(2300);
  });

  it("gives up after the retry budget", async () => {
    const fetch = vi.fn<FetchLike>(async () => limited());
    const { api, sleep } = client(fetch);
    await expect(api.getSnapshot(1)).rejects.toMatchObject({
      code: "rate_limited",
      retryAfterS: 2,
    });
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(sleep).toHaveBeenCalledTimes(3);
  });

  it("does not wait out long limits", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      json(429, errorBody("rate_limited", "Slow down."), { "Retry-After": "600" }),
    );
    const { api, sleep } = client(fetch);
    await expect(api.getSnapshot(1)).rejects.toMatchObject({ retryAfterS: 600 });
    expect(sleep).not.toHaveBeenCalled();
  });

  it("never retries build requests", async () => {
    const fetch = vi.fn<FetchLike>(async () => limited());
    const { api, sleep } = client(fetch);
    await expect(api.buildRepo("https://github.com/o/r")).rejects.toMatchObject({
      code: "rate_limited",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });
});

describe("error helpers", () => {
  it("parses Retry-After seconds and dates", () => {
    expect(parseRetryAfter("17")).toBe(17);
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("soon")).toBeNull();
    const now = Date.parse("2026-11-08T10:00:00Z");
    expect(parseRetryAfter("Sun, 08 Nov 2026 10:01:30 GMT", now)).toBe(90);
  });

  it("describes errors for people", () => {
    expect(describeError(new ApiError(403, "repo_private", "x")).title).toContain("private");
    expect(
      describeError(new ApiError(429, "rate_limited", "x", { group: "build" }, 42)).detail,
    ).toContain("42 seconds");
    expect(
      describeError(new ApiError(429, "rate_limited", "x", { upstream: "github" }, 600)).detail,
    ).toContain("10 minutes");
    expect(describeError(new ApiError(404, "not_found", "x", { resource: "repo" })).title).toBe(
      "Repository not found",
    );
    expect(describeError(new ApiError(500, "internal", "Boom", {}, null, "r-9")).detail).toBe(
      "Boom (request r-9)",
    );
    expect(describeError(new Error("x")).title).toBe("Something went wrong");
  });
});
