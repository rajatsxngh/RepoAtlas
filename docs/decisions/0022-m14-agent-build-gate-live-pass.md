# 0022 — M14 without spend: the agent's build gate, and what moved

**Status:** Accepted · **Date:** 2026-11-15

## Context

M14 was planned with three checks that call the model: the live injection suite in agent mode
(zero leaks), the agent-versus-one-shot report on the test split (three runs each, 95%
intervals, cost per question), and tuning `max_steps` and the per-tool budgets on the dev split.
Decision 0020 says the build spends nothing, so none of them can run now. This record applies
the same replan M12 and M13 used; nothing about the product changes.

Two design points of the agent also touch contracts of earlier milestones, and are recorded
here.

## Decision

1. **The product is unchanged by the constraint.** The tools, the loop with its budgets and
   routing, the evidence ledger, agent mode in the API and the web app, and the MCP server are
   built as designed. In a deployment the agent calls Claude through `LLMClient` (decision
   0010): `small` for exploration steps, `large` for the final answer.
2. **The M14 gate is a build gate.** Everything is tested without a model:
   - tools, validation, trimming, the loop, the caps, and the ledger with a scripted `FakeLLM`;
   - the request shapes of a whole agent conversation (tool definitions, tool results, thinking
     blocks passed back, the final structured call) by replaying a hand-written cassette
     (`"source": "docs"`) through the real `LLMClient`;
   - the injection suite in agent mode, end to end through the eval runner on the built fixture
     snapshot, with two scripted stand-ins. The *obedient* one follows every planted
     instruction it reads, including a tool call outside the snapshot, and the scorer must
     catch it. The *careful* one must score zero leaks over three runs. As in decision 0021,
     this proves that planted text reaches the agent's prompts marked, that the allowlist
     refuses the call, and that the scorer and the command work. It says nothing about how the
     real model behaves;
   - the evaluation path: `agent` and `oneshot` run through the runner on the same questions
     with stand-ins, and `repoatlas eval compare` reports the paired difference per question
     type with 95% intervals and the cost per question. The numbers of such a run measure the
     stand-ins, not the systems, and are not reported as results;
   - the MCP server with the SDK's in-memory client, over stdio as a subprocess, and over
     Streamable HTTP (token and rate limit). MCP tools call no model, so the manual check from
     Claude Code costs nothing and stays in this gate.
3. **The live checks move to the live pass,** pending in [`docs/live-pass.md`](../live-pass.md)
   and in M14's file, never passed:
   - live injection suite in agent mode: zero canary leaks, three runs per question;
   - the agent-versus-one-shot report on the test split, with intervals and cost;
   - the agent cassette re-recorded from the API (replacing the hand-written one);
   - `max_steps` and the token budgets checked on the dev split (the defaults below are
     reasoned, not tuned);
   - a question asked in agent mode in the running app with a live model.
   The gate rule of decision 0021 stands: agent mode ships only with zero leaks on the live
   suite. Until then it is not deployed anywhere.
4. **Running them later is configuration plus one command each** (see the live-pass file).
5. **The offline stand-in learns to play an agent** (extends decision 0020, item 3). Given a
   request with the agent's tools, `OfflineTransport` makes one `search_code` call with the
   question and then calls `final_answer`; given the final structured request, it fills the
   draft from the evidence block headers, as before. Usage stays zero. It exists so the
   documented commands, the end-to-end stack, and the self-host check can exercise agent mode
   without a key; it is still refused when a key is set.

### Agent defaults (untuned)

| Setting | Default | Why |
|---|---|---|
| `agent_max_steps` | 12 | The planned cap. |
| `agent_max_input_tokens` | 200,000 | Input tokens summed over every call of one answer. Twelve steps with 2,000-token results stay far below it; it stops a conversation that grows unexpectedly. |
| `agent_per_tool_result_tokens` | 2,000 | About 150 lines of code per result; `read_file` returns at most 200 lines. |
| `agent_max_cost_usd` | 0.25 | The same per-answer budget as one-shot answers, so cost per question compares like with like. |

### How the agent finishes (design)

The `small` model explores with tool calls. It ends exploration by calling `final_answer` (a
tool with no arguments) or by replying without a tool call. The `large` model then receives the
same conversation, unchanged and in order, plus one closing instruction, and fills the same
`AnswerDraft` schema as one-shot answers, citing ledger IDs. Tool choice is `auto` for
exploration and `none` for the final call (current models reject forced tool use, decision
0010). Thinking blocks are passed back exactly as returned; a block the other model can't read
is dropped by the API, not by us.

### Contract additions in earlier milestones

All are backward compatible; the milestone files list them under "Added later by decision
0022".

- **Answer contracts (M07).** `AnswerResult` gains `tool_uses: list[ToolUse(step, name,
  arguments, allowed, error, evidence_ids, result_tokens)]` (empty for modes without tools).
  `LLM.complete` accepts `tool_choice` (`auto` or `none`). `OfflineTransport` plays the agent
  as described above.
- **Ask API (M04, M07).** `mode` accepts `agent`; the stream gains `step` events. A stored run
  has mode `agent`. The cost and rate limits of a question apply unchanged.
- **Eval runner (M10).** The answer system `agent` is registered; `SystemOutput.tool_calls` is
  filled from `AnswerResult.tool_uses`.
- **Settings.** `agent_*` above, and for the MCP server `mcp_token` and
  `mcp_calls_per_minute`.

## Consequences

- M14 closes as Done on its build criteria with five live-pass items open.
- Whether the agent beats one-shot retrieval on multi-hop questions is unknown until the live
  pass. Nothing in the docs or the app claims it.
- The untuned defaults may be wrong for real questions; the live pass checks them on dev before
  the test-split report.
- The offline stand-in now has a second script to keep working; a unit test and the
  end-to-end stack cover it.
