# RepoAtlas benchmark

Questions about five pinned open-source repos, each with ground truth from a source that doesn't
depend on RepoAtlas. Later milestones score RepoAtlas against it. Methodology:
[decision 0013](../../docs/decisions/0013-evaluation-methodology.md) and PRD §10.

| File | What it holds |
|---|---|
| `questions.jsonl` | Every question, one canonical JSON object per line, sorted by ID (schema: `repoatlas.bench.schema.Question`) |
| `splits.json` | Dev and test IDs, the generation seed for each repo, the sha256 of the whole file and of the test questions, and whether the test split is frozen |
| `flow/<repo>.md` | Hand-written flow questions, plus the [authoring guide](flow/README.md) |
| `audit/audit-<version>.md` | The manual audit sheet for that version |

## Repos

The commits, recipes, and artifacts come from the sandbox ([`sandbox/recipes/`](../../sandbox/recipes/), [`eval/artifacts/`](../artifacts/)). Stars were read on 2026-11-12, the day the repos were pinned. Popular repos may be partly memorized by LLMs, and the closed-book baseline measures how much.

| Repo | Language | Commit | GitHub stars | SCIP indexer | Real impact data |
|---|---|---|---|---|---|
| click | Python | `cf19179e8e2f` | 17,820 | scip-python 0.6.8 | yes (33 mutated functions with failures) |
| requests | Python | `6e83187b8feb` | 54,685 | scip-python 0.6.8 | no |
| httpx | Python | `b5addb64f016` | 15,789 | scip-python 0.6.8 | yes (36) |
| zod | TypeScript | `c7ec94d309ee` | 44,328 | scip-typescript 0.6.2 | no |
| remeda | TypeScript | `ec4d3f5dad11` | 5,452 | scip-typescript 0.6.2 | yes (36) |

## Questions

130 questions, 26 per repo:

| Type | Per repo | Ground truth | Source |
|---|---|---|---|
| lookup | 6 | definition location of the symbol's name | SCIP definitions |
| callers | 6 | every caller (2 to 15) | SCIP references (rule below) |
| multihop | 4 | tests reaching X through at most one intermediate (3 per repo); modules using code from files mostly changed by one author (1 per repo) | SCIP, plus commit history |
| impact | 4 | transitive callers up to depth 3 (tests included); confirmed failing tests where the sandbox mutated the target | SCIP, plus sandbox mutations |
| flow | 4 | 3–8 must-cite symbols and a reference outline | people reading the code |
| unanswerable | 2 | refusal: one about a name that doesn't exist, one about facts outside the code | absence check, templates |

Splits are stratified by repo and type: 40 dev (31%) and 90 test. Provenance: 89 `scip`, 12 `sandbox`, 4 `git`, 25 `human` (20 flow questions and 5 outside-the-code unanswerable questions).

## How the questions are generated

```bash
cd backend
uv run repoatlas bench generate --repo all --seed 1   # writes questions.jsonl and splits.json
uv run repoatlas bench validate                       # schema, IDs, every location at the pinned commit
uv run repoatlas bench digest                         # sha256 of the file and of its test split
uv run repoatlas bench audit --sample 0.1             # writes audit/audit-<version>.md
uv run repoatlas bench split --freeze                 # freezes the test split (needs reviewed flow files)
```

`generate` reads only pinned inputs:
- each repo's files at the commit, from the sandbox's git cache;
- `index.scip`, checked against the sha256 in `scip.json`;
- `mutations.jsonl`;
- the full non-merge history behind the commit (a blobless clone);
- `flow/<repo>.md`.

Each generator uses its own seeded RNG over sorted inputs, so regenerating from the same artifacts gives the same bytes. `index.scip` is local-only (gitignored, rebuilt with `repoatlas sandbox scip`), so regeneration runs on a machine that has the artifacts, not in CI.

### Ground-truth rules

- **Symbols and locations.** A symbol is named by its symbol key (PRD §7.3). Its location is the path and the 1-based line of its name in SCIP's definition occurrence. `parse_file` is used only to turn that location into a key.
- **Caller of X.** The innermost enclosing definition of a non-import reference to X.
  - The enclosing definition comes from SCIP's enclosing ranges, skipping definitions that aren't a function, method, class, or test.
  - When SCIP gives no such scope, the caller is the innermost parsed function, method, class, or test whose span contains the reference. This covers TypeScript test callbacks and object-literal methods such as zod's `const X = $constructor(…, { m() { … } })`. The reference itself always comes from SCIP.
  - The indexers don't set SCIP's import role, so references inside `import` statements are found in the source (Python `ast`; TypeScript top-level `import`/`export … from` statements) and excluded.
  - A reference to a class followed by `(` also counts as a call to its constructor.
  - A "reference" at another definition's name (scip-python lists the second and later `@overload` definitions that way) is a redefinition, not a call.
  - Callers of overriding methods (SCIP `is_implementation`) are listed in `notes`, not in the answer.
- **Targets.** Top-level functions and classes, and methods directly in a class. They come from non-test library files, not from examples, docs, scripts, or benchmarks. Dunder names are excluded, and so are names shorter than 3 characters. Questions name the bare symbol when the repo defines that name once, or `Class.method` when that's unique. Samples are stratified by fan-in.
- **Ownership.** A file is *mostly changed by* an author who made more than half of the non-merge commits that touched it (at least 3), counting every commit up to the pinned commit. Only display names are used.
- **Real impact.** These are confirmed failing tests from the sandbox mutation runs, which failed twice and weren't quarantined. Test IDs that didn't map to a symbol key are kept with `symbol_key: null`. None of the 12 joined impact questions has unmapped tests (`bench generate` prints the counts).
- **Unanswerable names.** These don't appear anywhere in the repo's files or SCIP symbols at the pinned commit.

## Known limitations

- SCIP misses dynamic references: `getattr`, string dispatch, and calls through untyped values. Callers of overrides are listed separately in `notes`, but dynamic callers can still be missing from an answer.
- Any non-import reference counts, including references in type positions (a parameter annotated with the target class, a TypeScript overload signature). That matches the PRD's definition of a caller, but it's broader than "calls".
- Module-level references have no caller.
- Static impact stops at depth 3, so its sets can be large (up to a few hundred definitions for core functions).
- requests and zod have no real impact data. The sandbox didn't mutate them because their suites take 81 s and 150 s per run.
- The outside-the-code unanswerable questions come from four templates, so their wording repeats across repos.

## Audit

A seeded 10% sample of generated questions (11 of 110, `--seed 1`) is checked by hand against the code on GitHub. Mismatches are fixed in the generator and the sample is audited again.

| Round | Questions | Result |
|---|---|---|
| Generator self-check | 11 | Found one generator gap: zod's object-literal methods had no SCIP scope, so their references had no caller (for example, `ZodString` calling `_jwt`). Fixed by the parsed-span fallback above, then regenerated. |
| Owner audit | 11 | pending |

## Version history

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-12 | First version: 130 questions over 5 repos. Test split not frozen yet (flow review and audit pending). |
