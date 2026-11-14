import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AnswerView, Citation } from "@/lib/api/types";
import { renderWithApi } from "@/test/render";
import { AnswerBody, flagWarning } from "./AnswerBody";

function citation(claim: string, evidence: string, flags?: string[]): Citation {
  return {
    claim_id: claim,
    evidence_id: evidence,
    ref: {
      blob_oid: "a".repeat(40),
      path: "src/lab/billing.py",
      start_line: 3,
      end_line: 9,
      symbol_key: null,
    },
    ...(flags ? { flags } : {}),
  };
}

const ANSWER: AnswerView = {
  run_id: 1,
  answer_markdown: "It charges the card [c1]. It logs the result [c2].",
  claims: [
    { id: "c1", text: "charge calls the gateway.", evidence_ids: ["E1"] },
    { id: "c2", text: "log_result writes a line.", evidence_ids: ["E2"] },
  ],
  citations: [
    citation("c1", "E1", ["instruction_to_assistant", "canary_like", "<b>new_kind</b>"]),
    citation("c2", "E2", []),
  ],
  refused: false,
  refusal_kind: null,
  refusal_reason: null,
  usage: { input_tokens: 1000, output_tokens: 200, cache_read_tokens: 0, cache_write_tokens: 0 },
  cost_usd: 0.004,
  trace_id: null,
  metrics: {
    prefilter_citation_validity: 1,
    dropped_claim_rate: 0,
    citations_total: 2,
    citations_valid: 2,
    claims_total: 2,
    claims_dropped: 0,
    evidence_count: 6,
    evidence_flagged: 2,
    retrieval_top_score: 0.03,
  },
};

describe("AnswerBody", () => {
  it("marks citations of flagged code with a warning, and only those", async () => {
    const onCite = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<AnswerBody answer={ANSWER} onCite={onCite} />);
    const [flagged, clean] = within(screen.getByRole("list", { name: "Claims" })).getAllByRole(
      "button",
    );
    expect(flagged).toHaveAttribute("data-flagged", "true");
    const icon = within(flagged!).getByRole("img");
    expect(icon).toHaveAccessibleName(/written to steer an AI/);
    expect(icon).toHaveAccessibleName(/instructions addressed to an AI, a marker token/);
    expect(flagged).toHaveAttribute("title", expect.stringContaining("treated as data"));
    expect(flagged).toHaveTextContent("billing.py:3–9");
    // An unknown kind is shown as text, never as markup.
    expect(icon).toHaveAccessibleName(/<b>new_kind<\/b>/);
    expect(document.querySelector("b")).toBeNull();

    expect(clean).not.toHaveAttribute("data-flagged");
    expect(within(clean!).queryByRole("img")).toBeNull();
    expect(clean).toHaveAttribute("title", "src/lab/billing.py, lines 3–9");

    // A flagged citation still opens like any other.
    await user.click(flagged!);
    expect(onCite).toHaveBeenCalledWith(ANSWER.citations[0]);
  });

  it("says how many code excerpts were flagged, when any were", () => {
    const { rerender } = renderWithApi(<AnswerBody answer={ANSWER} onCite={() => {}} />);
    expect(screen.getByTestId("answer-metrics")).toHaveTextContent(
      "2 of 6 code excerpts flagged as instruction-like",
    );
    const none = { ...ANSWER, metrics: { ...ANSWER.metrics, evidence_flagged: 0 } };
    rerender(<AnswerBody answer={none} onCite={() => {}} />);
    expect(screen.getByTestId("answer-metrics")).not.toHaveTextContent("flagged");
  });

  it("treats a citation without flags (an older API) as unflagged", () => {
    const old = { ...ANSWER, citations: [citation("c1", "E1")] };
    renderWithApi(<AnswerBody answer={old} onCite={() => {}} />);
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("words every kind", () => {
    expect(flagWarning(["tool_request", "exfiltration_url"])).toContain(
      "a request to call a tool, a link built to carry data out",
    );
    expect(flagWarning(["role_or_system_claim", "encoded_payload"])).toContain(
      "a fake system or role message, an encoded payload",
    );
  });
});
