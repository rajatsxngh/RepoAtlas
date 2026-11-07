/**
 * The worker's logic, apart from `self`, so tests can drive it: requests are handled one at a
 * time, and a request that a newer one has replaced while it waited is skipped.
 */

import type { ELK } from "elkjs/lib/elk-api";
import { layoutContainers } from "./layoutEngine";
import { isLayoutRequest, type LayoutResponse } from "./layoutProtocol";

export function createLayoutService(
  elk: ELK,
  post: (response: LayoutResponse) => void,
  now: () => number = () => performance.now(),
): (message: unknown) => Promise<void> {
  let newest = 0;
  let queue: Promise<void> = Promise.resolve();
  return (message) => {
    if (!isLayoutRequest(message)) return Promise.resolve();
    newest = Math.max(newest, message.requestId);
    queue = queue.then(async () => {
      // Let messages that arrived meanwhile register first.
      await new Promise((resolve) => setTimeout(resolve, 0));
      if (message.requestId < newest) {
        post({ type: "skipped", requestId: message.requestId });
        return;
      }
      const started = now();
      try {
        const layouts = await layoutContainers(elk, message.containers);
        post({
          type: "result",
          requestId: message.requestId,
          layouts,
          ms: Math.round(now() - started),
        });
      } catch (error) {
        post({
          type: "error",
          requestId: message.requestId,
          message: error instanceof Error ? error.message : String(error),
        });
      }
    });
    return queue;
  };
}
