# 0010 — Claude via LiteLLM with model routing and tracing

**Status:** Accepted · **Date:** 2026-11-07

## Context

Answers, agents, verification, and judging need an LLM. Cost should stay low and visible. Self-hosters may want other providers later. Current Claude models may differ from older ones in which parameters and patterns they accept.

## Decision

- All LLM calls go through one `LLMClient` built on LiteLLM.
- Roles map to models in settings: `small` → `claude-haiku-5-5` (exploration, cheap steps), `large` → `claude-sonnet-5-5` (final answers, writer, verifier, checkers).
- Before any answer code, a compatibility spike checks the current Claude documentation (load the claude-api skill first) and the pinned LiteLLM version for: tool-choice modes, sampling parameters, thinking blocks (passed back unchanged), structured outputs and schema limits (flat schemas, validated with Pydantic), refusal stop reasons, prompt-caching limits. Each behavior gets a recorded-response test.
- Conversations are append-only: earlier turns are never edited or reordered.
- Every call is traced (tokens, cache tokens, cost, latency, stop reason) from the first LLM feature on. Prices live in configuration.

## Consequences

- One place to adapt when models or LiteLLM change; recorded tests catch regressions.
- Cost per question is measurable from day one.
- Switching providers is possible but not a v1 goal.

## Addendum — compatibility spike (M07 T1, 2026-11-12)

Checked against the current Claude documentation (claude-api skill, models cached 2026-11-03) and LiteLLM 1.106.5 (pinned exactly; the newest release at least two weeks old). The rules are code in `repoatlas.llm.compat`; each has a test in `tests/unit/llm/test_compat.py`, and the response behaviors replay cassettes in `tests/llm_cassettes/compat/` that `tests/live/test_llm_compat.py` records.

- **Which LiteLLM interface.** `LLMClient` sends through `litellm.anthropic.messages.acreate`, which forwards an Anthropic Messages body unchanged (checked: `system` blocks with `cache_control`, `output_config`, and `thinking` arrive as written) and returns the raw response, `stop_details` and thinking signatures included. The OpenAI-format `litellm.acompletion` translation was not used: it renames and drops fields for these models. LiteLLM's own retries are off (`num_retries=0`), its bundled price map is used instead of a network fetch, and prices come from settings. Errors keep their HTTP status (`status_code`); 529 overload arrives as 500.
- **Tool choice.** Claude Sonnet 5.5 rejects forced `any` and `tool` with a 400. Only `auto` and `none` are allowed; `build_request` refuses the others before sending. Callers name the tool in the prompt and check that a call was made.
- **Sampling parameters.** Non-default `temperature`, `top_p`, and `top_k` return a 400 on Claude Sonnet 5.5 and Claude Haiku 5.5. They are never sent.
- **Thinking.** Adaptive thinking is on by default on both models and can't be disabled with `{"type": "disabled"}` on Claude Sonnet 5.5, so `thinking` is not sent; depth is set with `output_config.effort` (defaults in settings: `low` for `small`, `medium` for `large`). Thinking text is omitted by default; blocks carry a signature and are passed back exactly as returned (`LLMResult.blocks` / `assistant_message`).
- **Structured outputs.** `output_config.format` with `type: json_schema`. Schemas are sent flat: `$ref` inlined, recursion refused, every object `additionalProperties: false` with all properties required (optional fields are nullable), and keyword constraints removed (`minLength`, `maxItems`, `minimum`, `pattern`, `default`, …). Pydantic validates the parsed output with the full constraints; on failure the client appends one repair turn (ANS-2).
- **Refusals.** `stop_reason: "refusal"` (HTTP 200) with `stop_details.category`. Surfaced as `LLMResult.refused`; answers report `refusal_kind = "api"`. The server-side `fallbacks` parameter (beta) is deliberately not used: a fallback model would answer instead of surfacing the refusal, and LiteLLM's own `fallbacks` keyword collides with it. Revisit in the backlog.
- **Prompt caching.** One breakpoint on the last system block (the static rules and output instructions); at most 4 per request; prefixes under 512 tokens (both models) silently don't cache, so the answer system prompt is kept above that. Cache reads and writes are read from `usage`.
- **Append-only conversations.** Earlier turns are never edited or reordered: `build_request` deep-copies, the repair turn appends the model's blocks and a new user message, and a test checks that the second request starts with the first one's messages.
- **Pricing (per million tokens, settings defaults).** Claude Sonnet 5.5: input $2.00, output $10.00, cache read $0.10, cache write $2.50. Claude Haiku 5.5: input $0.10, output $0.50, cache read $0.01, cache write $0.125 (prompts up to 100K tokens; answers stay far below that).

Cassettes marked `"source": "docs"` were written from the documented shapes and are replaced when the live tests run; `refusal.json` stays docs-shaped because a refusal is never triggered on purpose.

**Live check (2026-11-12, `uv run poe record-cassettes`, $0.014 in all).** Every case re-recorded from the API, with these refinements:
- Forced `tool_choice: any` on Claude Sonnet 5.5: the API's 400 (`tool_choice: type "tool" and "any" are not supported`), as documented.
- `temperature: 0.2` on Claude Haiku 5.5 is refused by LiteLLM before sending (`UnsupportedParamsError`, mapped to a 400 here): only the default is accepted. Same outcome; nothing sends it.
- Adaptive thinking is truly adaptive: at effort `low`, simple prompts (arithmetic, a short JSON answer, a tool call) came back with no thinking block at all. The round-trip case uses effort `high` and a harder prompt; its thinking block had empty text (display omitted by default) and a signature, and passing it back unchanged was accepted on the next turn.
- Caching: the second identical call read the whole 1,872-token static prefix from the cache (Haiku 5.5). The recorded answer on `py_small` wrote 1,383 cached tokens for the answer system prompt.
- LiteLLM's Anthropic pass-through routes any request with tools through its MCP-gateway check, which imports the optional `mcp` package. The transport passes `_skip_mcp_handler=True` (our tools are never LiteLLM proxy references), so tool calls go straight to the API.
