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

## Addendum — name-matching candidates (2026-11-07)

Measured on `pallets/flask` at `49943b5` and `colinhacks/zod` at `19c40d2` while building the first graphs. "Exactly one class or module defines `m`" is read narrowly, because the wide reading mostly matched builtin methods:

- T5/T6 match **instance members** only: methods of classes (getters, setters, and properties excluded, since they are read, not called) and functions assigned onto objects or prototypes (`res.json = function`, `Router.prototype.route = function`). Module-level, object-literal, and namespace functions are reached through import bindings (T1/T2); as candidates they caught `items.map()`, `x.toString()`, and `arr.find()` (9% of zod's T5/T6 edges, nearly all wrong).
- Members defined in test files are candidates only for calls from test files, and classes defined inside functions only for calls in their own file.
- A call whose receiver resolves, to a module, a class, or `super()`, but lacks the member becomes an unresolved call instead of a name match.
- Seeded hand check, 5 edges per tier: T1–T4 were 35 of 35 correct across both repos; T5 was 0/5 on zod (its v4 API attaches methods to instances at runtime, so v3 classes are the only static definers) and 4/5 on flask; T6 was 1/5 on both, as expected when one call spreads over several candidates.

## Addendum — refit confidences (2026-11-12)

The tier confidences were refit on calibration repos outside the benchmark: see decision 0017 (`TIERS_VERSION` 2).
