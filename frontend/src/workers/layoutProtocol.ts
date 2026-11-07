/** Messages between the canvas and the layout worker. */

import type { ContainerInput, ContainerLayout } from "./layoutEngine";

export interface LayoutRequest {
  type: "layout";
  /** Increases with every request; the worker skips requests a newer one has replaced. */
  requestId: number;
  /** Children before parents. */
  containers: ContainerInput[];
}

export type LayoutResponse =
  | { type: "result"; requestId: number; layouts: ContainerLayout[]; ms: number }
  | { type: "skipped"; requestId: number }
  | { type: "error"; requestId: number; message: string };

export function isLayoutRequest(value: unknown): value is LayoutRequest {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { type?: unknown }).type === "layout" &&
    typeof (value as { requestId?: unknown }).requestId === "number" &&
    Array.isArray((value as { containers?: unknown }).containers)
  );
}
