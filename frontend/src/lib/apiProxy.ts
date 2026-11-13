/**
 * The same-origin API proxy: the web server forwards `/api/v1/*` to the RepoAtlas API at
 * `REPOATLAS_API_ORIGIN` (read at run time, so one build works with any API address).
 *
 * The browser then talks to one origin, so the HTTP-only access cookie covers API calls, and
 * the API itself needs no public address. The proxy adds the access token as a Bearer header
 * (the browser never holds it) and tells the API the visitor's address for per-client limits.
 * Bodies stream in both directions, so server-sent events from the ask endpoint arrive as
 * they are produced.
 *
 * Build with `NEXT_PUBLIC_API_URL=/` to make the browser use it.
 */

import { accessToken } from "./access";

export const API_PREFIX = "/api/v1";
const REQUEST_HEADERS = ["accept", "content-type", "x-request-id"];
const RESPONSE_HEADERS = [
  "content-type",
  "cache-control",
  "retry-after",
  "x-request-id",
  "x-accel-buffering",
  "www-authenticate",
];

type Env = Record<string, string | undefined>;

/** The API's origin from `REPOATLAS_API_ORIGIN`, without a trailing slash; `null` when unset. */
export function apiOrigin(env: Env = process.env): string | null {
  const origin = env.REPOATLAS_API_ORIGIN?.trim().replace(/\/+$/, "");
  return origin ? origin : null;
}

/** An error in the API's error model. */
export function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message, details: {} } }, { status });
}

/**
 * The visitor's address as the nearest proxy reported it: `x-real-ip`, else the last entry of
 * `x-forwarded-for` (earlier entries are whatever the caller sent). Directly exposed, without
 * a reverse proxy in front, a caller can set these headers; see docs/self-hosting.md.
 */
export function clientAddress(headers: Headers): string | null {
  const real = headers.get("x-real-ip")?.trim();
  if (real) return real;
  const forwarded = headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  return forwarded ? forwarded : null;
}

/** The upstream URL for a request to this site's `/api/v1/...`, path and query kept exactly
 * as sent (symbol keys are percent-encoded path segments and must not be decoded here). */
export function upstreamUrl(requestUrl: string, origin: string): string | null {
  const url = new URL(requestUrl);
  if (url.pathname !== API_PREFIX && !url.pathname.startsWith(`${API_PREFIX}/`)) return null;
  return `${origin}${url.pathname}${url.search}`;
}

export async function forward(
  request: Request,
  env: Env = process.env,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const origin = apiOrigin(env);
  if (origin === null) {
    return errorResponse(
      503,
      "upstream_unavailable",
      "This web server has no API to forward to: REPOATLAS_API_ORIGIN is not set.",
    );
  }
  const target = upstreamUrl(request.url, origin);
  if (target === null) return errorResponse(404, "not_found", "No such API route.");

  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const token = accessToken(env);
  if (token !== null) headers.set("authorization", `Bearer ${token}`);
  const address = clientAddress(request.headers);
  if (address !== null) headers.set("x-forwarded-for", address);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    signal: request.signal,
    cache: "no-store",
    redirect: "manual",
  };
  if (hasBody) init.duplex = "half";

  let upstream: Response;
  try {
    upstream = await fetchImpl(target, init);
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return errorResponse(502, "upstream_unavailable", "The API could not be reached.");
  }
  const responseHeaders = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}
