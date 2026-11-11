/**
 * Mock mode's answers: no model, just the shapes `POST /snapshots/{id}/ask` streams. A question
 * that names a function, method, or class of the fixture gets an answer citing that symbol's
 * lines (and its file's other top symbols); any other question is refused for lack of evidence.
 */

import type { AnswerView, ApiNode, Citation, Claim } from "../types";
import type { MockGraph } from "./graph";

const SYMBOL_KINDS = ["function", "method", "class", "test"] as const;
const IDENTIFIER = /[A-Za-z_][A-Za-z0-9_]{2,}/g;

function citedSymbol(graph: MockGraph, question: string): ApiNode | null {
  for (const word of question.match(IDENTIFIER) ?? []) {
    const hit = graph
      .search(word, [...SYMBOL_KINDS], 5)
      .find((match) => match.match === "exact" && match.node.span);
    if (hit) return hit.node;
  }
  return null;
}

function citation(graph: MockGraph, node: ApiNode, claimId: string, evidenceId: string): Citation {
  const span = node.span!;
  const slice = graph.code(node.path, span.start_line, span.end_line);
  return {
    claim_id: claimId,
    evidence_id: evidenceId,
    ref: {
      blob_oid: slice.blob_oid,
      path: node.path,
      start_line: span.start_line,
      end_line: span.end_line,
      symbol_key: node.key,
    },
  };
}

const USAGE = {
  input_tokens: 2400,
  output_tokens: 310,
  cache_read_tokens: 1100,
  cache_write_tokens: 0,
};

export function mockAnswer(graph: MockGraph, question: string, runId: number): AnswerView {
  const node = citedSymbol(graph, question);
  const traceId = `00000000-0000-4000-8000-${String(runId).padStart(12, "0")}`;
  if (!node) {
    return {
      run_id: runId,
      answer_markdown: "The indexed code doesn't show enough to answer this question.",
      claims: [],
      citations: [],
      refused: true,
      refusal_kind: "evidence",
      refusal_reason: "No code in the snapshot matched the question.",
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
      cost_usd: 0,
      trace_id: traceId,
      metrics: {
        prefilter_citation_validity: null,
        dropped_claim_rate: null,
        citations_total: 0,
        citations_valid: 0,
        claims_total: 0,
        claims_dropped: 0,
        evidence_count: 0,
        retrieval_top_score: 0.01,
      },
    };
  }
  const claims: Claim[] = [
    {
      id: "c1",
      text: `\`${node.qualified_name}\` is a ${node.kind} defined in ${node.path}.`,
      evidence_ids: ["E1"],
      symbol: node.name,
    },
  ];
  const citations = [citation(graph, node, "c1", "E1")];
  return {
    run_id: runId,
    answer_markdown: `\`${node.qualified_name}\` is defined in \`${node.path}\` [c1]. This is a mock answer: mock mode has no model.`,
    claims,
    citations,
    refused: false,
    refusal_kind: null,
    refusal_reason: null,
    usage: USAGE,
    cost_usd: 0.0062,
    trace_id: traceId,
    metrics: {
      prefilter_citation_validity: 1,
      dropped_claim_rate: 0,
      citations_total: 1,
      citations_valid: 1,
      claims_total: 1,
      claims_dropped: 0,
      evidence_count: 8,
      retrieval_top_score: 0.033,
    },
  };
}

/** A server-sent event stream body. */
export function sseBody(events: readonly (readonly [string, unknown])[]): string {
  return events
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}
