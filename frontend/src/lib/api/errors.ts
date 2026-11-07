/** Errors from the API: the server's error model, plus the client's own network failures. */

import type { ErrorCode } from "./types";

/** Server codes (stable, PRD API-1) and two the client adds. */
export type ClientErrorCode = ErrorCode | "network" | "bad_response";

const SERVER_CODES: ReadonlySet<string> = new Set<ErrorCode>([
  "invalid_request",
  "invalid_url",
  "repo_private",
  "repo_too_large",
  "repo_empty",
  "not_found",
  "rate_limited",
  "snapshot_not_ready",
  "upstream_unavailable",
  "internal",
]);

export class ApiError extends Error {
  override readonly name = "ApiError";

  constructor(
    /** HTTP status; 0 when no response arrived. */
    readonly status: number,
    readonly code: ClientErrorCode,
    message: string,
    readonly details: Record<string, unknown> = {},
    /** Seconds the server asked to wait (`Retry-After`), if it said. */
    readonly retryAfterS: number | null = null,
    readonly requestId: string | null = null,
  ) {
    super(message);
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** `Retry-After` as seconds: delta-seconds or an HTTP date. */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number.parseInt(trimmed, 10);
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

/** The error a failed response stands for, from its `{"error": {...}}` body when it has one. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  const retryAfterS = parseRetryAfter(response.headers.get("Retry-After"));
  const requestId = response.headers.get("X-Request-ID");
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const error =
    body && typeof body === "object" && "error" in body ? (body as { error: unknown }).error : null;
  if (error && typeof error === "object") {
    const { code, message, details } = error as Record<string, unknown>;
    if (typeof code === "string" && SERVER_CODES.has(code) && typeof message === "string") {
      return new ApiError(
        response.status,
        code as ErrorCode,
        message,
        details && typeof details === "object" ? (details as Record<string, unknown>) : {},
        retryAfterS,
        requestId,
      );
    }
  }
  const fallback: ClientErrorCode =
    response.status === 429
      ? "rate_limited"
      : response.status === 404
        ? "not_found"
        : response.status >= 500
          ? "internal"
          : "bad_response";
  return new ApiError(
    response.status,
    fallback,
    `The API answered ${response.status} ${response.statusText}`.trim(),
    {},
    retryAfterS,
    requestId,
  );
}

function waitText(seconds: number | null): string {
  if (seconds === null) return "a little while";
  if (seconds < 90) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

export interface ErrorDescription {
  title: string;
  detail: string;
}

/** Words for people. Server messages are written for people and are shown as text. */
export function describeError(error: unknown): ErrorDescription {
  if (!isApiError(error)) {
    return { title: "Something went wrong", detail: "An unexpected error happened. Try again." };
  }
  switch (error.code) {
    case "invalid_url":
      return { title: "That URL won't work", detail: error.message };
    case "repo_private":
      return {
        title: "This repository is private",
        detail: "RepoAtlas maps public GitHub repositories only.",
      };
    case "repo_too_large":
      return { title: "This repository is too large", detail: error.message };
    case "repo_empty":
      return { title: "This repository is empty", detail: error.message };
    case "not_found":
      return {
        title: error.details.resource === "repo" ? "Repository not found" : "Not found",
        detail: error.message,
      };
    case "rate_limited":
      return error.details.upstream === "github"
        ? {
            title: "GitHub is rate limiting RepoAtlas",
            detail: `Try again in ${waitText(error.retryAfterS)}.`,
          }
        : {
            title: "Too many requests",
            detail: `You've hit the request limit. Try again in ${waitText(error.retryAfterS)}.`,
          };
    case "upstream_unavailable":
      return { title: "GitHub didn't answer", detail: error.message };
    case "snapshot_not_ready":
      return { title: "The map is still being built", detail: error.message };
    case "network":
      return { title: "Can't reach RepoAtlas", detail: error.message };
    case "invalid_request":
      return { title: "The request was refused", detail: error.message };
    case "internal":
    case "bad_response":
      return {
        title: "The server had a problem",
        detail: error.requestId
          ? `${error.message} (request ${error.requestId})`
          : `${error.message}`,
      };
  }
}
