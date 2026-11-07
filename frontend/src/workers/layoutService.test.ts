import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";
import { createInlineLayoutClient } from "@/components/graph/layoutClient";
import type { LayoutResponse } from "./layoutProtocol";
import { createLayoutService } from "./layoutService";

const container = (id: string, count = 3) => ({
  id,
  label: id,
  children: Array.from({ length: count }, (_, i) => ({ id: `${id}-${i}`, width: 150, height: 44 })),
  edges: [],
});

describe("layout service (the worker's logic)", () => {
  it("answers a request with layouts and timing", async () => {
    const posted: LayoutResponse[] = [];
    const handle = createLayoutService(new ELK(), (response) => posted.push(response));
    await handle({ type: "layout", requestId: 1, containers: [container("a")] });
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({ type: "result", requestId: 1 });
    if (posted[0]!.type === "result") {
      expect(Object.keys(posted[0]!.layouts[0]!.positions)).toHaveLength(3);
      expect(posted[0]!.ms).toBeGreaterThanOrEqual(0);
    }
  });

  it("skips requests that a newer one replaced while they waited", async () => {
    const posted: LayoutResponse[] = [];
    const handle = createLayoutService(new ELK(), (response) => posted.push(response));
    const first = handle({ type: "layout", requestId: 1, containers: [container("a")] });
    const second = handle({ type: "layout", requestId: 2, containers: [container("b")] });
    await Promise.all([first, second]);
    expect(posted.map((r) => [r.type, r.requestId])).toEqual([
      ["skipped", 1],
      ["result", 2],
    ]);
  });

  it("reports errors and ignores other messages", async () => {
    const posted: LayoutResponse[] = [];
    const broken = {
      layout: async () => Promise.reject(new Error("boom")),
    } as unknown as InstanceType<typeof ELK>;
    const handle = createLayoutService(broken, (response) => posted.push(response));
    await handle({ hello: "world" });
    await handle({ type: "layout", requestId: 3, containers: [container("a")] });
    expect(posted).toEqual([{ type: "error", requestId: 3, message: "boom" }]);
  });
});

describe("layout client", () => {
  it("resolves stale requests to null and applies only the latest", async () => {
    const client = createInlineLayoutClient();
    const older = client.request([container("a")]);
    const newer = client.request([container("b")]);
    expect(await older).toBeNull();
    const result = await newer;
    expect(result?.layouts.map((layout) => layout.id)).toEqual(["b"]);
    client.dispose();
  });
});
