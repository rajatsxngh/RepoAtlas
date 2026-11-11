import { describe, expect, it } from "vitest";
import { ApiClient } from "./client";
import { ApiError } from "./errors";
import { readEvents, type ServerEvent } from "./sse";

function chunked(...parts: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(encoder.encode(part));
      controller.close();
    },
  });
}

async function all(stream: ReadableStream<Uint8Array>): Promise<ServerEvent[]> {
  const events: ServerEvent[] = [];
  for await (const event of readEvents(stream)) events.push(event);
  return events;
}

describe("readEvents", () => {
  it("parses events split across chunks, CRLF lines, and comments", async () => {
    const events = await all(
      chunked(
        ": keep-alive\n\nevent: sta",
        'tus\ndata: {"step":"retrieving"}\n\n',
        'event: answer\r\ndata: {"a":\r\ndata: 1}\r\n\r\n',
        "data: plain",
      ),
    );
    expect(events).toEqual([
      { event: "status", data: { step: "retrieving" } },
      { event: "answer", data: { a: 1 } },
      { event: "message", data: "plain" },
    ]);
  });

  it("keeps multi-byte characters split across chunks", async () => {
    const bytes = new TextEncoder().encode('data: {"t":"café"}\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 15));
        controller.enqueue(bytes.slice(15));
        controller.close();
      },
    });
    expect(await all(stream)).toEqual([{ event: "message", data: { t: "café" } }]);
  });
});

describe("ApiClient.ask", () => {
  function client(response: () => Response) {
    const requests: { url: string; init?: RequestInit }[] = [];
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async (url, init) => {
        requests.push({ url, init });
        return response();
      },
    });
    return { api, requests };
  }

  it("posts the question, reports steps, and returns the answer", async () => {
    const { api, requests } = client(
      () =>
        new Response(
          chunked(
            'event: status\ndata: {"step":"retrieving"}\n\n',
            'event: answer\ndata: {"refused":true}\n\n',
            'event: done\ndata: {"run_id":4,"trace_id":null}\n\n',
          ),
        ),
    );
    const steps: string[] = [];
    const outcome = await api.ask(
      7,
      { question: "q", mode: "oneshot" },
      { onStatus: (step) => steps.push(step) },
    );
    expect(requests[0]!.url).toBe("http://x/api/v1/snapshots/7/ask");
    expect(requests[0]!.init?.method).toBe("POST");
    expect(JSON.parse(String(requests[0]!.init?.body))).toEqual({ question: "q", mode: "oneshot" });
    expect(steps).toEqual(["retrieving"]);
    expect(outcome.answer).toEqual({ refused: true });
    expect(outcome.done).toEqual({ run_id: 4, trace_id: null });
  });

  it("throws error responses and error events as ApiErrors", async () => {
    const refused = client(() =>
      Response.json(
        { error: { code: "llm_unavailable", message: "No key.", details: {} } },
        { status: 503 },
      ),
    );
    await expect(refused.api.ask(1, { question: "q", mode: "oneshot" })).rejects.toMatchObject({
      status: 503,
      code: "llm_unavailable",
    });
    const failed = client(
      () =>
        new Response(
          chunked(
            'event: error\ndata: {"error":{"code":"answer_failed","message":"busy","details":{}}}\n\n',
          ),
        ),
    );
    const error = await failed.api.ask(1, { question: "q", mode: "oneshot" }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "answer_failed", message: "busy" });
    const empty = client(
      () => new Response(chunked('event: status\ndata: {"step":"answering"}\n\n')),
    );
    await expect(empty.api.ask(1, { question: "q", mode: "oneshot" })).rejects.toMatchObject({
      code: "bad_response",
    });
  });
});
