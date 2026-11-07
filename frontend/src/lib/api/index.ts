/**
 * The app's API client: the real API, or fixtures from memory in mock mode
 * (`NEXT_PUBLIC_API_MOCK=1`). The mock code loads only in mock mode.
 */

import { config } from "@/lib/config";
import { ApiClient, type FetchLike } from "./client";

export { ApiClient } from "./client";
export { ApiError, describeError, isApiError } from "./errors";
export type * from "./types";

type RateLimitListener = (waitS: number) => void;
const rateLimitListeners = new Set<RateLimitListener>();

/** Hear about 429 waits (for a "slowing down" notice); returns the unsubscribe function. */
export function onRateLimited(listener: RateLimitListener): () => void {
  rateLimitListeners.add(listener);
  return () => rateLimitListeners.delete(listener);
}

function lazyMockFetch(): FetchLike {
  let real: Promise<FetchLike> | null = null;
  return async (input, init) => {
    real ??= import("./mock/fetch").then(({ createMockFetch }) => createMockFetch());
    return (await real)(input, init);
  };
}

let client: ApiClient | null = null;

export function getApiClient(): ApiClient {
  client ??= new ApiClient({
    baseUrl: config.mock ? "http://mock.repoatlas.invalid/api/v1" : `${config.apiUrl}/api/v1`,
    fetch: config.mock ? lazyMockFetch() : undefined,
    onRateLimited: (waitS) => rateLimitListeners.forEach((listener) => listener(waitS)),
  });
  return client;
}
