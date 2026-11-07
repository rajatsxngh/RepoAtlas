/**
 * Talks to the layout worker. Each request gets a new ID; a result that arrives after a newer
 * request was sent is stale and resolves to null, so only the latest layout is applied.
 */

import ELK from "elkjs/lib/elk.bundled.js";
import type { ContainerInput, ContainerLayout } from "@/workers/layoutEngine";
import type { LayoutRequest, LayoutResponse } from "@/workers/layoutProtocol";
import { createLayoutService } from "@/workers/layoutService";

export interface LayoutResult {
  requestId: number;
  layouts: ContainerLayout[];
  ms: number;
}

export interface LayoutClient {
  request(containers: ContainerInput[]): Promise<LayoutResult | null>;
  dispose(): void;
}

type Resolver = { resolve: (result: LayoutResult | null) => void; reject: (error: Error) => void };

function channel(send: (request: LayoutRequest) => void) {
  let latest = 0;
  const pending = new Map<number, Resolver>();
  const receive = (response: LayoutResponse) => {
    const waiting = pending.get(response.requestId);
    if (!waiting) return;
    pending.delete(response.requestId);
    if (response.type === "error") waiting.reject(new Error(response.message));
    else if (response.type === "skipped" || response.requestId !== latest) waiting.resolve(null);
    else
      waiting.resolve({
        requestId: response.requestId,
        layouts: response.layouts,
        ms: response.ms,
      });
  };
  const request = (containers: ContainerInput[]) => {
    latest += 1;
    const requestId = latest;
    return new Promise<LayoutResult | null>((resolve, reject) => {
      pending.set(requestId, { resolve, reject });
      send({ type: "layout", requestId, containers });
    });
  };
  const fail = (error: Error) => {
    for (const waiting of pending.values()) waiting.reject(error);
    pending.clear();
  };
  return { receive, request, fail };
}

/** Lays out in this thread: for tests and browsers without workers. */
export function createInlineLayoutClient(elk: InstanceType<typeof ELK> = new ELK()): LayoutClient {
  let handle: (message: unknown) => Promise<void> = async () => {};
  const link = channel((request) => void handle(request));
  handle = createLayoutService(elk, link.receive);
  return {
    request: link.request,
    dispose: () => link.fail(new Error("The layout client was closed.")),
  };
}

/** Lays out in a web worker; null where workers are not available. */
export function createWorkerLayoutClient(): LayoutClient | null {
  if (typeof Worker === "undefined") return null;
  let worker: Worker;
  try {
    worker = new Worker(new URL("../../workers/layout.worker.ts", import.meta.url), {
      type: "module",
    });
  } catch {
    return null;
  }
  const link = channel((request) => worker.postMessage(request));
  worker.addEventListener("message", (event: MessageEvent<LayoutResponse>) =>
    link.receive(event.data),
  );
  worker.addEventListener("error", (event) =>
    link.fail(new Error(event.message || "The layout worker failed.")),
  );
  return {
    request: link.request,
    dispose: () => {
      link.fail(new Error("The layout client was closed."));
      worker.terminate();
    },
  };
}

let shared: LayoutClient | null = null;

/** One layout worker for the page (in this thread where workers are unavailable). */
export function getLayoutClient(): LayoutClient {
  shared ??= createWorkerLayoutClient() ?? createInlineLayoutClient();
  return shared;
}
