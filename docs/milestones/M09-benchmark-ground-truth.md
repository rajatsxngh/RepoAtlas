# M09 — Benchmark & ground truth

**Type of work:** Measurement dataset · **Size:** M (8 tasks) · **Status:** In progress

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
- [ ] **T5 — Real impact import.** Join `mutations.jsonl` into impact questions; count unmapped tests.
- [ ] **T6 — Flow and unanswerable questions.** Authoring guide and template; write and review flow questions (human step); unanswerable generator with absence checks.
- [ ] **T7 — Splits and determinism.** Stratified split, freeze, digests; test that regeneration is identical.
- [ ] **T8 — Audit and documentation.** 10% audit recorded; benchmark README with version, counts by repo and type, and known limitations.

## Exit criteria

- [ ] At least 100 validated questions across 5 repos, including flow and unanswerable questions, balanced as above.
- [ ] Regenerating from the same artifacts produces an identical digest.
- [ ] The 10% manual audit passes (mismatches fixed at the generator level and re-audited).
- [ ] The test split is frozen, with its sha256 recorded in `splits.json` and in Results.
- [ ] Impact questions with real failure data exist for at least 2 repos.

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

_Filled in at the gate._
