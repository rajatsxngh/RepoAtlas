import { describe, expect, it } from "vitest";
import { buildSteps, isActive, stageLabel } from "./stages";

const states = (steps: ReturnType<typeof buildSteps>) => steps.map((s) => `${s.stage}:${s.state}`);

describe("build steps", () => {
  it("marks stages before, at, and after the current one", () => {
    expect(states(buildSteps({ status: "running", stage: "parse" }, []))).toEqual([
      "fetch:done",
      "inventory:done",
      "parse:current",
      "graph:pending",
      "store:pending",
    ]);
    expect(states(buildSteps({ status: "queued", stage: null }, []))).toEqual([
      "fetch:pending",
      "inventory:pending",
      "parse:pending",
      "graph:pending",
      "store:pending",
    ]);
  });

  it("shows where a build failed and when it finished", () => {
    expect(states(buildSteps({ status: "failed", stage: "graph" }, []))).toContain("graph:failed");
    expect(
      buildSteps({ status: "succeeded", stage: "store" }, []).every((s) => s.state === "done"),
    ).toBe(true);
  });

  it("keeps stages the app doesn't know, in the order seen", () => {
    const steps = buildSteps({ status: "running", stage: "index_chunks" }, ["embed"]);
    expect(steps.map((s) => s.stage)).toEqual([
      "fetch",
      "inventory",
      "parse",
      "graph",
      "store",
      "embed",
      "index_chunks",
    ]);
    expect(steps.at(-2)!.state).toBe("done");
    expect(steps.at(-1)!).toMatchObject({ state: "current", label: "Index chunks" });
  });

  it("labels stages for people", () => {
    expect(stageLabel("fetch")).toBe("Fetching the source from GitHub");
    expect(stageLabel("embed")).toBe("Embed");
    expect(stageLabel("")).toBe("Working");
    expect(isActive(undefined)).toBe(true);
    expect(isActive({ status: "failed" })).toBe(false);
  });
});
