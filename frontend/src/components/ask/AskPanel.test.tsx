import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "@/lib/api/client";
import type { AnswerView, Citation } from "@/lib/api/types";
import { mockApi, renderWithApi } from "@/test/render";
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
    evidence_flagged: 0,
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

describe("AskPanel in agent mode", () => {
  it("offers three modes and asks in the chosen one", async () => {
    const bodies: unknown[] = [];
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return streamOf([["answer", HOSTILE]]);
      },
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    const modes = screen.getByRole("radiogroup", { name: "Answer mode" });
    expect(
      within(modes)
        .getAllByRole("radio")
        .map((radio) => (radio as HTMLInputElement).value),
    ).toEqual(["oneshot", "agent", "multiagent"]);
    expect(within(modes).getByRole("radio", { name: "Quick" })).toBeChecked();
    expect(screen.getByTestId("ask-mode-hint")).toHaveTextContent("One search of the code");
    await user.type(screen.getByLabelText("Question"), "q");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByRole("region", { name: "Answer" });
    await user.click(within(modes).getByRole("radio", { name: "Agent" }));
    expect(within(modes).getByRole("radio", { name: "Agent" })).toBeChecked();
    expect(screen.getByTestId("ask-mode-hint")).toHaveTextContent("Explores the code with tools");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies).toEqual([
      { question: "q", mode: "oneshot" },
      { question: "q", mode: "agent" },
    ]);
    // A quick answer has no steps.
    expect(screen.queryByRole("region", { name: "Agent steps" })).toBeNull();
  });

  it("shows each tool call, then the answer with its citations", async () => {
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />);
    await user.click(screen.getByRole("radio", { name: "Agent" }));
    await user.type(screen.getByLabelText("Question"), "How does create_app work?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const answer = await screen.findByRole("region", { name: "Answer" });
    const steps = screen.getByRole("region", { name: "Agent steps" });
    const rows = within(steps).getAllByRole("listitem");
    expect(rows).toHaveLength(5);
    expect(rows[0]).toHaveTextContent("search_code");
    expect(rows[0]).toHaveTextContent('query="How does create_app work?"');
    expect(rows[0]).toHaveTextContent("5 pieces of evidence");
    expect(rows[1]).toHaveTextContent("find_definition");
    expect(rows[2]).toHaveTextContent("read_file");
    expect(rows[3]).toHaveTextContent("Finished exploring");
    expect(rows[4]).toHaveTextContent("Wrote the answer");
    expect(rows[4]).toHaveTextContent("claude-sonnet-5-5");
    expect(within(steps).getByTestId("steps-summary")).toHaveTextContent("3 tool calls · $0.0133");
    expect(steps).toHaveAttribute("aria-busy", "false");
    // The answer's cost is the steps' cost, and its citation opens like any other.
    expect(within(answer).getByTestId("cost-badge")).toHaveTextContent("$0.0133");
    await user.click(within(answer).getByRole("button", { name: "app.py:22–23" }));
    expect(await screen.findByRole("figure")).toHaveTextContent("def create_app");
  });

  it("shows steps while the agent is still working, and clears them on the next question", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const step = {
      seq: 1,
      step: 1,
      kind: "tool",
      tool: "search_code",
      arguments: 'query="cart"',
      result_tokens: 640,
      evidence: 3,
      error: null,
      allowed: true,
      cached: false,
      truncated: false,
      flags: [],
      model: "claude-haiku-5-5",
      tokens_in: 3000,
      tokens_out: 60,
      cost_usd: 0.0003,
      span_id: null,
    };
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () => {
        const encoder = new TextEncoder();
        const send = (event: string, data: unknown) =>
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        const body = new ReadableStream<Uint8Array>({
          async start(controller) {
            controller.enqueue(send("status", { step: "exploring" }));
            controller.enqueue(send("step", step));
            await gate;
            controller.enqueue(send("answer", HOSTILE));
            controller.close();
          },
        });
        return new Response(body, { status: 200 });
      },
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    await user.click(screen.getByRole("radio", { name: "Agent" }));
    await user.type(screen.getByLabelText("Question"), "q");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const steps = await screen.findByRole("region", { name: "Agent steps" });
    expect(steps).toHaveAttribute("aria-busy", "true");
    expect(within(steps).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Exploring the code");
    expect(screen.queryByRole("region", { name: "Answer" })).toBeNull();
    expect(screen.getByRole("radio", { name: "Quick" })).toBeDisabled();
    release();
    await screen.findByRole("region", { name: "Answer" });
    expect(screen.getByRole("region", { name: "Agent steps" })).toHaveAttribute(
      "aria-busy",
      "false",
    );
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

describe("AskPanel in multi-agent mode", () => {
  it("asks in multi-agent mode and shows the branches, the answer, and what was verified", async () => {
    const bodies: unknown[] = [];
    const { api } = mockApi();
    const ask = api.ask.bind(api);
    vi.spyOn(api, "ask").mockImplementation((snapshotId, request, options) => {
      bodies.push(request);
      return ask(snapshotId, request, options);
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    await user.click(screen.getByRole("radio", { name: "Multi-agent" }));
    expect(screen.getByTestId("ask-mode-hint")).toHaveTextContent("checks every claim");
    await user.type(screen.getByLabelText("Question"), "How does create_app work?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const answer = await screen.findByRole("region", { name: "Answer" });
    expect(bodies).toEqual([{ question: "How does create_app work?", mode: "multiagent" }]);

    const steps = screen.getByRole("region", { name: "Agent steps" });
    expect(within(steps).getByTestId("steps-summary")).toHaveTextContent(
      "2 branches · 3 tool calls",
    );
    const first = within(steps).getByRole("region", { name: "Branch b1" });
    expect(first).toHaveTextContent("Where is `create_app` defined, and what does it do?");
    expect(within(first).getAllByRole("listitem")).toHaveLength(4);
    expect(within(first).getByTestId("branch-state")).toHaveTextContent("explored");
    const second = within(steps).getByRole("region", { name: "Branch b2" });
    expect(second).toHaveTextContent("find_callers");
    expect(within(steps).getByRole("list", { name: "After the branches" })).toHaveTextContent(
      "Checked the claims against the code",
    );
    expect(steps).toHaveAttribute("aria-busy", "false");

    // The verified answer: one claim kept, one left out with the verifier's reason.
    const verification = within(answer).getByRole("group", { name: "Verification" });
    expect(verification).toHaveTextContent("1 of 2 claims verified against the code they cite");
    expect(verification).toHaveTextContent("1 left out");
    expect(within(verification).getByRole("list", { name: "Claims left out" })).toHaveTextContent(
      "not supported: The cited lines define the symbol",
    );
    await user.click(within(answer).getByRole("button", { name: "app.py:22–23" }));
    expect(await screen.findByRole("figure")).toHaveTextContent("def create_app");
  });

  it("shows the plan while branches are still being explored", async () => {
    let finish: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const encoder = new TextEncoder();
    const chunk = (event: string, data: unknown) =>
      encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    const api = new ApiClient({
      baseUrl: "http://x/api/v1",
      fetch: async () =>
        new Response(
          new ReadableStream({
            async start(controller) {
              controller.enqueue(chunk("status", { step: "planning" }));
              controller.enqueue(
                chunk("plan", {
                  branches: [
                    { id: "b1", sub_question: "Part one?", seed_symbols: [], max_steps: 3 },
                    { id: "b2", sub_question: "Part two?", seed_symbols: [], max_steps: 3 },
                  ],
                }),
              );
              controller.enqueue(chunk("status", { step: "exploring" }));
              await gate;
              controller.enqueue(chunk("answer", HOSTILE));
              controller.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
    });
    const user = userEvent.setup();
    renderWithApi(<AskPanel snapshotId={1} />, { api });
    await user.click(screen.getByRole("radio", { name: "Multi-agent" }));
    await user.type(screen.getByLabelText("Question"), "q");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("region", { name: "Branch b2" })).toHaveTextContent("Part two?");
    expect(screen.getByRole("status")).toHaveTextContent("Exploring the code…");
    expect(screen.getByRole("region", { name: "Agent steps" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getAllByTestId("branch-state")[0]).toHaveTextContent("waiting…");
    finish();
    await screen.findByRole("region", { name: "Answer" });
    // An answer without verification (not a multi-agent one) shows no verification box.
    expect(screen.queryByRole("group", { name: "Verification" })).toBeNull();
  });
});
