/** Build stages as people read them. Stages the app doesn't know yet still show, by name. */

import type { JobView } from "@/lib/api/types";

export const KNOWN_STAGES: readonly string[] = ["fetch", "inventory", "parse", "graph", "store"];

const LABELS: Record<string, string> = {
  fetch: "Fetching the source from GitHub",
  inventory: "Listing source files",
  parse: "Parsing the code",
  graph: "Linking imports and calls",
  store: "Saving the map",
};

export function stageLabel(stage: string): string {
  if (LABELS[stage]) return LABELS[stage];
  const words = stage.replace(/[_-]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : "Working";
}

export type StepState = "done" | "current" | "pending" | "failed";

export interface Step {
  stage: string;
  label: string;
  state: StepState;
}

/** The known stages plus any new ones seen, in order, with the job's position among them. */
export function buildSteps(
  job: Pick<JobView, "status" | "stage">,
  seen: readonly string[],
): Step[] {
  const order = [...KNOWN_STAGES];
  for (const stage of seen) if (!order.includes(stage)) order.push(stage);
  if (job.stage && !order.includes(job.stage)) order.push(job.stage);
  const at = job.stage ? order.indexOf(job.stage) : -1;
  return order.map((stage, index) => {
    let state: StepState;
    if (job.status === "succeeded") state = "done";
    else if (at < 0 || index > at) state = "pending";
    else if (index < at) state = "done";
    else
      state = job.status === "failed" ? "failed" : job.status === "running" ? "current" : "pending";
    return { stage, label: stageLabel(stage), state };
  });
}

export function isActive(job: Pick<JobView, "status"> | undefined): boolean {
  return !job || job.status === "queued" || job.status === "running";
}
