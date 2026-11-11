import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "@/lib/api/client";
import type { AnswerView, Citation } from "@/lib/api/types";
import { renderWithApi } from "@/test/render";
import { AskPanel, citationLabel, citationLayer } from "./AskPanel";

function streamOf(events: [string, unknown][]): Response {
  const body = events
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const CITATION: Citation = {
  claim_id: "c1",
  evidence_id: "E1",
  ref: {
    blob_oid: "a".repeat(40),
    path: "src/shop/cart.py",
    start_line: 17,
    end_line: 19,
    symbol_key: "py:src/shop/cart.py:Cart.total:method",
  },
};

const HOSTILE: AnswerView = {
  run_id: 1,
  answer_markdown:
    "<img src=x onerror=alert(1)> **Total** sums items [c1]. [x](javascript:alert(2))",
  claims: [{ id: "c1", text: "<script>alert(3)</script> total sums prices", evidence_ids: ["E1"] }],
  citations: [CITATION],
  refused: false,
  refusal_kind: null,
  refusal_reason: null,
  usage: { input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0 },
  cost_usd: 0.01,
  trace_id: null,
  metrics: {
    prefilter_citation_validity: 0.5,
    dropped_claim_rate: 0.5,
    citations_total: 2,
    citations_valid: 1,
    claims_total: 2,
    claims_dropped: 1,
    evidence_count: 4,
    retrieval_top_score: 0.03,
  },
};

describe("AskPanel", () => {
  it("asks, shows the steps, and opens a citation in the code viewer", async () => {
    const onCite = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} onCite={onCite} />);
    const panel = screen.getByRole("complementary", { name: "Ask" });
    await user.type(within(panel).getByLabelText("Question"), "How does create_app work?");
    await user.click(within(panel).getByRole("button", { name: "Ask" }));
    const answer = await within(panel).findByRole("region", { name: "Answer" });
    expect(answer).toHaveTextContent("create_app");
    const claims = within(answer).getByRole("list", { name: "Claims" });
    const chip = within(claims).getByRole("button", { name: "app.py:22–23" });
    expect(within(answer).getByTestId("answer-metrics")).toHaveTextContent(
      "1 of 1 citations valid before filtering (100%)",
    );
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(onCite).toHaveBeenCalledWith(
      expect.objectContaining({ ref: expect.objectContaining({ path: "src/shop/app.py" }) }),
    );
    const viewer = await within(panel).findByRole("figure");
    expect(viewer).toHaveTextContent("def create_app");
    expect(viewer.querySelectorAll(".ra-code-mark")).toHaveLength(2);
  });

  it("shows evidence refusals with their reason", async () => {
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />);
    await user.type(screen.getByLabelText("Question"), "how are payments refunded?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const answer = await screen.findByRole("region", { name: "Answer" });
    expect(answer).toHaveTextContent("Not enough evidence");
    expect(answer).toHaveTextContent("No code in the snapshot matched the question.");
    expect(within(answer).queryByRole("list", { name: "Claims" })).toBeNull();
  });

  it("reports the step that is running", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () => {
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          async start(controller) {
            controller.enqueue(encoder.encode('event: status\ndata: {"step": "answering"}\n\n'));
            await gate;
            controller.enqueue(
              encoder.encode(`event: answer\ndata: ${JSON.stringify(HOSTILE)}\n\n`),
            );
            controller.close();
          },
        });
        return new Response(body, { status: 200 });
      },
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    await user.type(screen.getByLabelText("Question"), "q");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Writing the answer");
    release();
    await screen.findByRole("region", { name: "Answer" });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders model and repo text as text, never as HTML", async () => {
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () => streamOf([["answer", HOSTILE]]),
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    await user.type(screen.getByLabelText("Question"), "q");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const answer = await screen.findByRole("region", { name: "Answer" });
    expect(answer.querySelector("img, script")).toBeNull();
    expect(answer.querySelector("a[href^='javascript']")).toBeNull();
    expect(answer).toHaveTextContent("<script>alert(3)</script> total sums prices");
    expect(within(answer).getByText("Total").tagName).toBe("STRONG");
    expect(answer).toHaveTextContent("1 claims dropped");
  });

  it("shows errors from the stream", async () => {
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () =>
        streamOf([
          ["status", { step: "retrieving" }],
          [
            "error",
            { error: { code: "answer_failed", message: "The model is busy.", details: {} } },
          ],
        ]),
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    await user.type(screen.getByLabelText("Question"), "q");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No answer this time");
    expect(alert).toHaveTextContent("The model is busy.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled());
  });
});

describe("citations", () => {
  it("are labeled by file and lines, and highlight their symbol", () => {
    expect(citationLabel(CITATION)).toBe("cart.py:17–19");
    expect(citationLabel({ ...CITATION, ref: { ...CITATION.ref, end_line: 17 } })).toBe(
      "cart.py:17",
    );
    expect(citationLayer(CITATION)).toEqual({
      id: "citation",
      nodeKeys: ["py:src/shop/cart.py:Cart.total:method"],
      style: "primary",
      label: "Cited: cart.py:17–19",
    });
    const noSymbol = { ...CITATION, ref: { ...CITATION.ref, symbol_key: null } };
    expect(citationLayer(noSymbol).nodeKeys).toEqual(["file:src/shop/cart.py"]);
  });
});
