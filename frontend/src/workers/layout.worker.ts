/// <reference lib="webworker" />
/** The layout worker: elkjs runs here so expanding a container never blocks the page. */

import ELK from "elkjs/lib/elk.bundled.js";
import type { LayoutResponse } from "./layoutProtocol";
import { createLayoutService } from "./layoutService";

const scope = self as unknown as DedicatedWorkerGlobalScope;
const handle = createLayoutService(new ELK(), (response: LayoutResponse) =>
  scope.postMessage(response),
);

scope.addEventListener("message", (event: MessageEvent<unknown>) => {
  void handle(event.data);
});
