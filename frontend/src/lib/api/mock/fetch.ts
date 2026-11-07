/** A `fetch` that answers from a `MockBackend` instead of the network, with a little latency. */

import type { FetchLike } from "../client";
import { MockBackend } from "./backend";

export interface MockFetchOptions {
  /** Added to every answer, plus up to half again as jitter (milliseconds). */
  latencyMs?: number;
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

function delay(ms: number, signal?: AbortSignal | null): Promise<void> {
  if (ms <= 0) return signal?.aborted ? Promise.reject(abortError(signal)) : Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(abortError(signal));
      },
      { once: true },
    );
  });
}

export function createMockFetch(
  backend: MockBackend = new MockBackend(),
  options: MockFetchOptions = {},
): FetchLike {
  const latency = options.latencyMs ?? 60;
  let requests = 0;
  return async (input, init) => {
    const url = new URL(input, "http://mock.repoatlas.invalid");
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = undefined;
      }
    }
    await delay(latency + Math.random() * latency * 0.5, init?.signal);
    const answer = await backend.handle(method, url, body);
    if (init?.signal?.aborted) throw abortError(init.signal);
    requests += 1;
    return new Response(JSON.stringify(answer.body), {
      status: answer.status,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": `mock-${requests}`,
        ...answer.headers,
      },
    });
  };
}
