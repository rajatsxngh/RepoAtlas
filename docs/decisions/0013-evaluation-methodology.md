# 0013 — Evaluation methodology

**Status:** Accepted · **Date:** 2026-11-07

## Context

Published accuracy is RepoAtlas's main claim, so the numbers must survive scrutiny. Common failure modes: scoring a system against its own graph, tuning on the test set, single runs with no variance, LLM judges favoring their own family, and models that already know popular repos.

## Decision

- **Independent ground truth:** structure from SCIP indexes built in the sandbox; real impact from mutation runs; ownership from commit history; flow questions from people reading code without RepoAtlas. RepoAtlas's parser only translates locations into symbol keys.
- **Splits:** about 30% dev and 70% test, stratified by repo and type; the test split is frozen and hashed; all tuning uses dev; every test-split run is kept.
- **Statistics:** three runs per system; paired bootstrap over questions (10,000 resamples) for 95% intervals.
- **Baselines** share the same model, prompt, and context budget: closed-book (also measures memorization), BM25-only, vector-only, hybrid GraphRAG; later agent and multi-agent.
- **Judges:** blind to system names, random order, length-controlled, checked against about 50 human labels; low agreement means exploratory status.
- **Calibration** of edge confidences uses repos outside the benchmark.
- **Reports** record benchmark version and digest, config fingerprints, model IDs, embedding model, code commit, and environment.

## Consequences

- Numbers that others can reproduce and trust, including published losses.
- More upfront work (sandbox, SCIP, hand-written flow questions) and evaluation cost, controlled by caching and cost caps.
