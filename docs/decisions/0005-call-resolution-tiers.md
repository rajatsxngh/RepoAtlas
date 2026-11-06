# 0005 — Name-matching call resolution with confidence tiers

**Status:** Accepted · **Date:** 2026-11-07

## Context

Tree-sitter shows structure, not which function a call actually reaches across files. Compiler-grade indexers (SCIP) resolve calls precisely but need each project's dependencies installed, which means running installs — something RepoAtlas does only inside the sandbox for curated repos.

## Decision

- Calls are resolved by name matching through import bindings and class hierarchies, using the tier table in PRD §9.1 (T1–T6 with initial confidences).
- Ambiguous calls with 2–5 candidates produce one low-confidence edge per candidate; more candidates, or none, produce an unresolved-call record instead of an edge.
- Calls to external packages and builtins are dropped and counted.
- SCIP is used for evaluation ground truth and for calibrating tier confidences on repos outside the benchmark. SCIP as a source of product edges is in the backlog.

## Consequences

- Graph builds need no dependency installs and work for any public repo.
- Precision is lower for dynamic code; confidences, the unresolved list, and the calibration report quantify it.
- Calibrated confidences are recorded in an addendum when they change.

## Addendum

_Calibrated tier confidences go here._
