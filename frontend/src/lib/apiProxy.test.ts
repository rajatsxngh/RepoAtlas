import { describe, expect, it, vi } from "vitest";
import { GET as healthz } from "@/app/healthz/route";
import { apiOrigin, clientAddress, forward, upstreamUrl } from "./apiProxy";
import { readConfig } from "./config";

const SITE = "http://web.test";
const ENV = { REPOATLAS_API_ORIGIN: "http://api.internal:8000/" };

function upstream(response: Response = Response.json({ ok: true })) {
  return vi.fn<typeof fetch>(async () => response);
}

/** The URL and options of the one request the proxy sent to the API. */
function sentRequest(
  fetchImpl: ReturnType<typeof upstream>,
): [string, RequestInit & { duplex?: string }] {
  const call = fetchImpl.mock.calls[0];
  if (!call) throw new Error("The API was not called.");
  return [String(call[0]), call[1] ?? {}];
}

describe("the same-origin API proxy", () => {
  it("is what the browser calls when the app is built with NEXT_PUBLIC_API_URL=/", () => {
    expect(readConfig({ NEXT_PUBLIC_API_URL: "/" }).apiUrl).toBe("");
  });

  it("reads the API origin at run time", () => {
    expect(apiOrigin(ENV)).toBe("http://api.internal:8000");
    expect(apiOrigin({})).toBeNull();
    expect(apiOrigin({ REPOATLAS_API_ORIGIN: " " })).toBeNull();
  });

  it("keeps the path and query exactly as sent, encoded symbol keys included", () => {
    const key = "py%3Asrc%2Fapp.py%3A%3Acreate_app%2520x";
    expect(
      upstreamUrl(
        `${SITE}/api/v1/snapshots/3/nodes/${key}/neighbors?kinds=calls&kinds=imports`,
        "http://api",
      ),
    ).toBe(`http://api/api/v1/snapshots/3/nodes/${key}/neighbors?kinds=calls&kinds=imports`);
    expect(upstreamUrl(`${SITE}/api/v2/x`, "http://api")).toBeNull();
    expect(upstreamUrl(`${SITE}/api/v1evil`, "http://api")).toBeNull();
  });

  it("forwards a read with the token and the visitor's address, and nothing else of theirs", async () => {
    const fetchImpl = upstream();
    const request = new Request(`${SITE}/api/v1/snapshots/1?x=1`, {
      headers: {
        accept: "application/json",
        cookie: "repoatlas_access=abc; other=1",
        authorization: "Bearer from-the-browser",
        "x-request-id": "req-1",
        "x-forwarded-for": "198.51.100.1, 203.0.113.9",
      },
    });
    const response = await forward(
      request,
      { ...ENV, REPOATLAS_ACCESS_TOKEN: "server-token" },
      fetchImpl,
    );
    expect(response.status).toBe(200);
    const [url, init] = sentRequest(fetchImpl);
    expect(url).toBe("http://api.internal:8000/api/v1/snapshots/1?x=1");
    const sent = new Headers(init.headers);
    expect(Object.fromEntries(sent.entries())).toEqual({
      accept: "application/json",
      authorization: "Bearer server-token",
      "x-forwarded-for": "203.0.113.9",
      "x-request-id": "req-1",
    });
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });

  it("sends no Authorization header when the deployment has no gate", async () => {
    const fetchImpl = upstream();
    await forward(new Request(`${SITE}/api/v1/ready`), ENV, fetchImpl);
    expect(new Headers(sentRequest(fetchImpl)[1].headers).has("authorization")).toBe(false);
  });

  it("streams a POST body up and server-sent events back", async () => {
    const events = "event: status\ndata: {}\n\nevent: done\ndata: {}\n\n";
    const fetchImpl = upstream(
      new Response(events, {
        headers: {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          "x-accel-buffering": "no",
          "x-request-id": "r2",
          "set-cookie": "leak=1",
          server: "uvicorn",
        },
      }),
    );
    const request = new Request(`${SITE}/api/v1/snapshots/1/ask`, {
      method: "POST",
      body: JSON.stringify({ question: "How?" }),
      headers: { "content-type": "application/json" },
    });
    const response = await forward(request, ENV, fetchImpl);
    const [, init] = sentRequest(fetchImpl);
    expect(init.method).toBe("POST");
    expect(init.duplex).toBe("half");
    expect(await new Response(init.body).text()).toBe('{"question":"How?"}');
    expect(await response.text()).toBe(events);
    expect(Object.fromEntries(response.headers.entries())).toEqual({
      "cache-control": "no-cache",
      "content-type": "text/event-stream",
      "x-accel-buffering": "no",
      "x-request-id": "r2",
    });
  });

  it("passes the API's errors through, with Retry-After", async () => {
    const limited = Response.json(
      { error: { code: "rate_limited", message: "Slow down.", details: {} } },
      { status: 429, headers: { "retry-after": "12" } },
    );
    const response = await forward(new Request(`${SITE}/api/v1/jobs/1`), ENV, upstream(limited));
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("12");
    expect((await response.json()).error.code).toBe("rate_limited");
  });

  it("answers in the error model when there is no API or it can't be reached", async () => {
    const unset = await forward(new Request(`${SITE}/api/v1/ready`), {}, upstream());
    expect(unset.status).toBe(503);
    expect((await unset.json()).error.code).toBe("upstream_unavailable");
    const down = vi.fn<typeof fetch>(async () => {
      throw new TypeError("fetch failed");
    });
    const failed = await forward(new Request(`${SITE}/api/v1/ready`), ENV, down);
    expect(failed.status).toBe(502);
    const body = await failed.json();
    expect(body.error.code).toBe("upstream_unavailable");
    expect(JSON.stringify(body)).not.toContain("api.internal");
  });
});

describe("clientAddress", () => {
  it.each([
    [{ "x-real-ip": "203.0.113.7", "x-forwarded-for": "1.1.1.1" }, "203.0.113.7"],
    [{ "x-forwarded-for": "198.51.100.1, 203.0.113.9" }, "203.0.113.9"],
    [{ "x-forwarded-for": "203.0.113.9" }, "203.0.113.9"],
    [{}, null],
  ])("reads %j as %j", (headers, expected) => {
    expect(clientAddress(new Headers(headers as Record<string, string>))).toBe(expected);
  });
});

describe("/healthz", () => {
  it("says the web server is up", async () => {
    const response = healthz();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
  });
});
