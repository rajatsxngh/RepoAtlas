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

## Addendum

_Compatibility-spike findings go here._
