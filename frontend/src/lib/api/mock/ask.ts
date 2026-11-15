/**
 * Mock mode's answers: no model, just the shapes `POST /snapshots/{id}/ask` streams. A question
 * that names a function, method, or class of the fixture gets an answer citing that symbol's
 * lines (and its file's other top symbols); any other question is refused for lack of evidence.
 *
 * In agent mode the same answer comes with the `step` events of a short exploration: a search,
 * a lookup and a read in one turn, the end of the exploration, and the final answer call.
 */

import type {
  AnswerView,
  ApiNode,
  AskStep,
  AskStepEvent,
  Citation,
  Claim,
  ToolUse,
} from "../types";
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
        evidence_flagged: 0,
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
      evidence_flagged: 0,
      retrieval_top_score: 0.033,
    },
  };
}

export interface MockAgentRun {
  answer: AnswerView;
  steps: AskStepEvent[];
  statuses: AskStep[];
}

const EXPLORE_MODEL = "claude-haiku-5-5";
const ANSWER_MODEL = "claude-sonnet-5-5";

function stepEvent(seq: number, step: number, fields: Partial<AskStepEvent>): AskStepEvent {
  return {
    seq,
    step,
    kind: "tool",
    tool: "",
    arguments: "",
    result_tokens: 0,
    evidence: 0,
    error: null,
    allowed: true,
    cached: false,
    truncated: false,
    flags: [],
    model: null,
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    span_id: null,
    ...fields,
  };
}

function toolUse(event: AskStepEvent, args: Record<string, unknown>, ids: string[]): ToolUse {
  return {
    step: event.step,
    name: event.tool,
    arguments: args,
    allowed: event.allowed,
    error: event.error,
    evidence_ids: ids,
    result_tokens: event.result_tokens,
    truncated: event.truncated,
    cached: event.cached,
    flags: event.flags,
  };
}

/**
 * A mock agent answer: the answer `mockAnswer` gives, with the steps that led to it. The steps'
 * tokens and costs add up to the answer's.
 */
export function mockAgentRun(graph: MockGraph, question: string, runId: number): MockAgentRun {
  const base = mockAnswer(graph, question, runId);
  const node = citedSymbol(graph, question);
  const turn = { model: EXPLORE_MODEL, tokens_in: 3100, tokens_out: 70, cost_usd: 0.0003 };
  const query = JSON.stringify(question.length > 60 ? `${question.slice(0, 57)}...` : question);
  const search = stepEvent(1, 1, {
    tool: "search_code",
    arguments: `query=${query}`,
    result_tokens: node ? 640 : 12,
    evidence: node ? 5 : 0,
    ...turn,
  });
  const usage = (events: AskStepEvent[]) => ({
    input_tokens: events.reduce((sum, event) => sum + event.tokens_in, 0),
    output_tokens: events.reduce((sum, event) => sum + event.tokens_out, 0),
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  });
  const cost = (events: AskStepEvent[]) =>
    Number(events.reduce((sum, event) => sum + event.cost_usd, 0).toFixed(6));
  if (!node) {
    // Nothing found: the exploration ends and no final answer call is made.
    const steps = [search, stepEvent(2, 2, { kind: "finish", tool: "final_answer", ...turn })];
    const answer: AnswerView = {
      ...base,
      refusal_reason: "The tools found no code in the snapshot for this question.",
      usage: usage(steps),
      cost_usd: cost(steps),
      tool_uses: [toolUse(search, { query: question }, [])],
    };
    return { answer, steps, statuses: ["exploring"] };
  }
  const span = node.span!;
  const lookup = stepEvent(2, 2, {
    tool: "find_definition",
    arguments: `name=${JSON.stringify(node.name)}`,
    result_tokens: 90,
    evidence: 1,
    ...turn,
    tokens_in: 3900,
    cost_usd: 0.0004,
  });
  const read = stepEvent(3, 2, {
    tool: "read_file",
    arguments: `path=${JSON.stringify(node.path)}, start=${span.start_line}, end=${span.end_line}`,
    result_tokens: 210,
    evidence: 1,
  });
  const finish = stepEvent(4, 3, {
    kind: "finish",
    tool: "final_answer",
    ...turn,
    tokens_in: 4300,
    cost_usd: 0.0005,
  });
  const final = stepEvent(5, 4, {
    kind: "answer",
    tool: "answer",
    model: ANSWER_MODEL,
    tokens_in: 4500,
    tokens_out: 310,
    cost_usd: 0.0121,
  });
  const steps = [search, lookup, read, finish, final];
  const citations = base.citations.map((citation) => ({ ...citation, evidence_id: "E6" }));
  const answer: AnswerView = {
    ...base,
    answer_markdown: `\`${node.qualified_name}\` is defined in \`${node.path}\` [c1]. This is a mock answer: mock mode has no model, and the steps above are made up.`,
    claims: base.claims.map((claim) => ({ ...claim, evidence_ids: ["E6"] })),
    citations,
    usage: usage(steps),
    cost_usd: cost(steps),
    metrics: { ...base.metrics, evidence_count: 7, retrieval_top_score: null },
    tool_uses: [
      toolUse(search, { query: question }, ["E1", "E2", "E3", "E4", "E5"]),
      toolUse(lookup, { name: node.name }, ["E6"]),
      toolUse(read, { path: node.path, start: span.start_line, end: span.end_line }, ["E7"]),
    ],
  };
  return { answer, steps, statuses: ["exploring", "answering", "validating"] };
}

/** A server-sent event stream body. */
export function sseBody(events: readonly (readonly [string, unknown])[]): string {
  return events
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}
