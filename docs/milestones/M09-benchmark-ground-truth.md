# M09 — Benchmark & ground truth

**Type of work:** Measurement dataset · **Size:** M (8 tasks) · **Status:** Done

## Goal

A versioned benchmark of at least 100 questions across five repos, whose answers come from sources independent of RepoAtlas (SCIP indexes, commit history, sandbox failures, and people reading code), with a frozen test split.

## Depends on

- **M08** — pinned benchmark repos and recipes, `index.scip` artifacts with indexer versions, `mutations.jsonl` (confirmed failing tests per mutated symbol key), `baseline.json`, artifact manifests.
- **M01** — `fetch_source`, `fetch_history`, `parse_file` and symbol keys (used only to translate definition locations into symbol keys), test-file detection.

## Provides

- **`eval/benchmark/`**
  - `questions.jsonl` — every question, one JSON object per line.
  - `splits.json` — dev and test question IDs, the sha256 of the test set, and the benchmark version.
  - `README.md` — how the benchmark was generated, its version history, and the audit results.
  - `flow/<repo_id>.md` — hand-written flow questions with must-cite symbols and reference outlines.
- **Question schema** (`repoatlas.bench.schema.Question`):
  ```json
  {
    "id": "click-callers-003",
    "benchmark_version": "1.0.0",
    "repo_id": "click", "commit": "<sha>",
    "type": "lookup | callers | multihop | flow | impact | unanswerable",
    "text": "Which functions call `Context.invoke`?",
    "ground_truth": { "...": "type-specific, below" },
    "split": "dev | test",
    "provenance": "scip | git | sandbox | human",
    "notes": ""
  }
  ```
  Ground truth by type:
  - `lookup`: `{ "definition": { "symbol_key", "path", "line" } }`
  - `callers`, `multihop`: `{ "answers": [ { "symbol_key", "path", "line" } ] }`
  - `impact`: `{ "target": {...}, "static": [ ... ], "real_failing_tests": [ { "test_id", "symbol_key" } ] | null }`
  - `flow`: `{ "must_cite": [ { "symbol_key", "path", "line" } ], "reference_outline": "..." }`
  - `unanswerable`: `{ "expected": "refusal" }`
- **`repoatlas.bench.scip`** — Python bindings generated from a pinned `scip.proto` (committed generated module) and `ScipIndex` helpers: `definitions()`, `references(symbol)`, `enclosing_definition(occurrence)`, `implementations(symbol)`, `documents()`.
- **Generators** (`repoatlas.bench.generate`): `gen_lookup`, `gen_callers`, `gen_multihop`, `gen_static_impact`, `import_real_impact`, `gen_unanswerable` — deterministic for a given seed.
- **CLI:** `repoatlas bench generate --repo <id> --seed <n>`, `repoatlas bench validate`, `repoatlas bench split --freeze`, `repoatlas bench audit --sample 0.1`, `repoatlas bench digest`.

## Requirements

BEN-1, BEN-2, BEN-3, BEN-4, BEN-5, BEN-6, BEN-7, BEN-8, BEN-9, BEN-10, BEN-11, BEN-12

## Scope

**In:** SCIP reading, question schema and validation, generators for each type, importing sandbox failures, hand-written flow questions, unanswerable questions, splits, determinism, manual audit, documentation.

**Out:** running any answering system, scoring, statistics.

## Design notes

### Independence
- Relationships (definitions, references, callers, implementations) come only from SCIP; ownership only from commit history; real impact only from sandbox runs; flow answers only from people reading code.
- RepoAtlas's parser is used only to turn a definition location (path + line) into a symbol key, so that ground truth and system outputs can be compared. It is never the source of a relationship.

### Exact definitions
- **Definition location:** the path and line of the symbol's name in SCIP's definition occurrence.
- **Caller of X:** the innermost enclosing definition of a non-import reference to X. scip-python attaches enclosing ranges to definitions, so the enclosing definition is found by position containment. Constructor calls count as calls to the class. Calls to an override are linked to the base method through SCIP's implementation relationships and listed separately in `notes`.
- **Targets** are sampled from SCIP definitions in non-test files (stratified by fan-in), then translated into symbol keys by location.

### Question types
- **Lookup** ("Where is `X` defined?"): unique names only (names defined once in the repo), or qualified names when not unique.
- **Callers** ("Which functions call `X`?"): targets with 2–15 callers, so lists are meaningful but checkable.
- **Multi-hop:** templates such as "Which test functions reach `X` directly or through exactly one intermediate function?" and "Which modules import code from files mostly changed by `<author>`?" (authors from `git log --name-only`, display names only).
- **Static impact:** "What breaks if I change `X`?" with ground truth = transitive callers up to depth 3 plus tests that reach X; when the target was mutated in the sandbox, `real_failing_tests` is filled from `mutations.jsonl`.
- **Flow:** "How does a request reach the database?", "What happens when the CLI parses an option?" — written by a person reading the code on GitHub, without using RepoAtlas, with a must-cite list of 3–8 symbols and a short reference outline; reviewed by a second pass.
- **Unanswerable:** questions about plausible but nonexistent symbols (names checked absent from the repo) or about behavior outside the code (deployment settings, runtime data). The correct outcome is a refusal.

### Balance and splits
- About 26 questions per repo: ~6 lookup, ~6 callers, ~4 multi-hop, ~4 flow, ~4 impact, ~2 unanswerable.
- Stratified split by repo and type: about 30% dev, 70% test. The test split is frozen: its sha256 is stored in `splits.json`, and any change to test questions requires a new benchmark version.

### Determinism and audit
- Generation uses fixed seeds and sorted inputs; `repoatlas bench digest` prints the sha256 of the canonical `questions.jsonl`. Two generations from the same artifacts must match.
- Audit: a random 10% of generated questions (seeded) is checked by hand against the code on GitHub; mismatches are fixed in the generator, not in the data, and the audit table goes into the benchmark README.

### Contamination
- Popular repos may be partly memorized by LLMs. This milestone records each repo's popularity (stars at pinning time) in the README so that later analysis can account for it.

## Tasks

- [x] **T1 — SCIP bindings and helpers.** Generate bindings from the pinned proto; `ScipIndex`; tests on a small committed SCIP fixture.
- [x] **T2 — Question schema and validator.** Pydantic model, per-type ground-truth models, `bench validate` (schema, references to existing paths and lines at the pinned commit).
- [x] **T3 — Lookup and callers generators.** Tests on the SCIP fixture.
- [x] **T4 — Multi-hop and static impact generators.**
- [x] **T5 — Real impact import.** Join `mutations.jsonl` into impact questions; count unmapped tests.
- [x] **T6 — Flow and unanswerable questions.** Authoring guide and template; write and review flow questions (human step); unanswerable generator with absence checks.
- [x] **T7 — Splits and determinism.** Stratified split, freeze, digests; test that regeneration is identical.
- [x] **T8 — Audit and documentation.** 10% audit recorded; benchmark README with version, counts by repo and type, and known limitations.

## Exit criteria

- [x] At least 100 validated questions across 5 repos, including flow and unanswerable questions, balanced as above.
- [x] Regenerating from the same artifacts produces an identical digest.
- [x] The 10% manual audit passes (mismatches fixed at the generator level and re-audited).
- [x] The test split is frozen, with its sha256 recorded in `splits.json` and in Results.
- [x] Impact questions with real failure data exist for at least 2 repos.

## Verification

```bash
cd backend
uv run pytest tests/unit/bench -q
uv run repoatlas bench generate --repo click --seed 1
uv run repoatlas bench validate
uv run repoatlas bench digest
uv run repoatlas bench audit --sample 0.1
```

## Risks and open questions

- SCIP misses some dynamic references; callers questions avoid targets with obvious dynamic dispatch, and limitations are documented.
- Hand-written flow questions take real effort; four per repo keeps it bounded.
- Open: whether to include a sixth, held-out repo used only once at the end as an extra overfitting check.

## Results

Gate checked on 2026-11-12 on branch `m09-benchmark` (CI run 43178069096 passed on every job), on Windows 11 with Postgres and Docker up.

**Tests.** In `backend/`, `uv run poe check` passes. Totals: 1,280 tests passed, 1 skipped (creating symlinks needs extra privileges on Windows), 7 deselected (the `live` tests). ruff, mypy (312 files), and the docs lint pass too. `tests/unit/bench` has 109 tests, built on two committed SCIP fixtures: a Python package, and a TypeScript subproject indexed from `/src/packages/lib`.

**Benchmark 1.0.0** is in [`eval/benchmark/`](../../eval/benchmark/), and its [README](../../eval/benchmark/README.md) has the rules, limitations, and audit.
- **Questions:** 130, 26 per repo (6 lookup, 6 callers, 4 multihop, 4 impact, 4 flow, 2 unanswerable), split 40 dev and 90 test.
- **Provenance:** 89 `scip`, 12 `sandbox`, 4 `git`, 25 `human`.
- **Questions sha256:** `c121bf8d5914228d4998dc96e9e5ea675ac1b356591a2bc18f2a34c4fc8a9577`.
- **SCIP bindings:** generated from `scip.proto` at sourcegraph/scip v0.10.0 (`1c2b6db7`) with grpcio-tools 1.85.1 and committed. Regenerate with `scripts/gen_scip_bindings.sh`.

**Exit criteria**
- *At least 100 validated questions across 5 repos, balanced.* `repoatlas bench validate` reports `130 questions valid (5 repos)`. It checks the schema, IDs, versions, sort order, canonical JSON, and every path, line, and name at the pinned commits. Every repo has 4 flow and 2 unanswerable questions.
- *Regenerating gives an identical digest.* `repoatlas bench generate --repo all --seed 1` ran twice from the same artifacts and wrote byte-identical files (`cmp` matched); `repoatlas bench digest` prints `c121bf8d…a9577` both times. The same check is a unit test on the fixtures (`test_split_determinism.py`).
- *The 10% manual audit passes.* The seeded sample was 11 of the 110 generated questions, in [`audit/audit-1.0.0.md`](../../eval/benchmark/audit/audit-1.0.0.md).
  - The owner delegated the audit, so Claude did it against the code at the pinned commits.
  - Rounds 1 and 2 found 5 generator problems: zod's object-literal methods, scip-python's mis-resolution of httpx's tests, property setters, merged TypeScript interface/const symbols, and tainted tests-reach intermediates. Each was fixed in the generator, and each round was re-audited after regenerating.
  - Round 3 passed 11 of 11. Two of those (click impact questions) pass with a documented limitation: SCIP can't see references through untyped pytest fixtures.
- *The test split is frozen.* `repoatlas bench split --freeze` printed `dev 40, test 90 (frozen)`. The test sha256 is `163c2957919f00015870f1697e9a140903077d0fb7273beabd2f57767dd4cd08`, recorded in `splits.json`. Regenerating after the freeze still writes the same file; changing a test question makes `generate` refuse.
- *Real impact data for at least 2 repos.* 12 impact questions, in click, httpx, and remeda, have confirmed failing tests from `mutations.jsonl`, with 0 unmapped test IDs.

**Flow questions.** AI agents drafted all 20 by reading only the source at the pinned commits. The review rule: must-cite lists keep only symbols that decide or transform something on the path, which removed 11 pass-throughs.
- The owner (rajatsinghh) reviewed click, requests, and httpx question by question.
- For zod and remeda, the owner delegated the review to Claude's check against the code; their files record that.

**Notes for later milestones**
- **Static and real impact disagree a lot in click.** Its tests use untyped fixtures, so for `Result.output`, 938 of 980 real failures are outside the static set. remeda's typed tests line up (all but 5 of 57). Report the two separately.
- **httpx.** scip-python 0.6.8 mis-resolves 712 of httpx's test references to ground-truth symbols. The benchmark ignores those references and avoids the affected names. The backlog has the fix.
- **Generation is local-only.** It needs `index.scip`, the sandbox git caches, and the history clones under `backend/.repoatlas/`, so CI checks the committed files and the fixtures, not a regeneration.
- **Tooling.** `repoatlas bench keys --repo <id> <path>` lists a file's symbol keys for authors of flow questions.
