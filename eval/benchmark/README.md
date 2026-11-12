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
| callers | 6 | every caller (2 to 15) | SCIP references (rules below) |
| multihop | 4 | tests reaching X through at most one intermediate (3 per repo); modules using code from files mostly changed by one author (1 per repo) | SCIP, plus commit history |
| impact | 4 | transitive callers up to depth 3 (tests included); confirmed failing tests where the sandbox mutated the target (12 questions in click, httpx, and remeda) | SCIP, plus sandbox mutations |
| flow | 4 | 3–8 must-cite symbols and a reference outline | people reading the code |
| unanswerable | 2 | refusal: one about a name that doesn't exist, one about facts outside the code | absence check, templates |

- **Splits:** stratified by repo and type, with 40 dev (31%) and 90 test. The test split is frozen; its sha256 is `163c2957919f00015870f1697e9a140903077d0fb7273beabd2f57767dd4cd08`.
- **Provenance:** 89 `scip`, 12 `sandbox`, 4 `git`, and 25 `human` (the 20 flow questions plus the 5 outside-the-code unanswerable questions).

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

Each generator uses its own seeded RNG over sorted inputs, so regenerating from the same artifacts gives the same bytes. `index.scip` is local-only (gitignored, rebuilt with `repoatlas sandbox scip`), so regeneration runs on a machine that has the artifacts, not in CI. Once the test split is frozen, `generate` refuses to write if any test question would change. Changing one needs a new benchmark version.

### Ground-truth rules

- **Symbols and locations.** A symbol is named by its symbol key (PRD §7.3). Its location is the path and the 1-based line of its name in SCIP's definition occurrence. `parse_file` is used only to turn locations into keys.
- **Caller of X.** The innermost enclosing definition of a non-import reference to X.
  - The enclosing definition comes from SCIP's enclosing ranges, skipping definitions that aren't a function, method, class, or test.
  - SCIP gives no definition to some callables:
    - TypeScript test callbacks;
    - object-literal methods such as zod's `const X = $constructor(…, { m() { … } })`;
    - Python property setters;
    - `@overload` stubs, which scip-python lists as references to the first definition.

    So when a parsed function, method, class, or test lies strictly inside SCIP's innermost scope, or SCIP has none, the caller is that parsed one. The reference itself always comes from SCIP.
  - The indexers don't set SCIP's import role, so references inside `import` statements are found in the source (Python `ast`; TypeScript top-level `import`/`export … from` statements) and excluded.
  - A reference to a class followed by `(` also counts as a call to its constructor.
  - A "reference" at another definition's name (a redefinition) is not a call.
  - Callers of overriding methods (SCIP `is_implementation`) are listed in `notes`, not in the answer.
- **Mis-resolved references are ignored.** A reference whose source text doesn't name its target is an indexer error, so it's dropped and counted. A reference names its target when the text is the target's name (or that name quoted), its class's name for a constructor, or a local alias (`import X as Y`, `{ X as Y }`, a default import). Ignored per repo: click 4, requests 0, httpx 712, zod 4, remeda 0.
  - scip-python 0.6.8 resolves many `httpx.<Name>` references in httpx's tests to a neighbour in the package's export list (`Response` to `Request`, `Client` to `AsyncClient`, and so on).
  - Targets are not sampled when their name or class appears on either side of a mis-resolved reference, because values typed through those names can have mis-resolved members too. For tests-reach questions, the same applies to every intermediate.
- **Targets.** Top-level functions and classes, and methods directly in a class, from non-test library files (not examples, docs, scripts, or benchmarks).
  - Dunder names are excluded, and so are names shorter than 3 characters.
  - TypeScript names that are also declared as an `interface` or `type` in the same file are excluded. SCIP merges those declarations, so type-only uses would count as calls.
  - Questions name the bare symbol when the repo defines that name once, or `Class.method` when that's unique.
  - Samples are stratified by fan-in.
- **Ownership.** A file is *mostly changed by* an author who made more than half of the non-merge commits that touched it (at least 3), counting every commit up to the pinned commit. Only display names are used.
- **Real impact.** These are confirmed failing tests from the sandbox mutation runs, which failed twice and weren't quarantined. Test IDs that didn't map to a symbol key are kept with `symbol_key: null`. None of the 12 joined impact questions has unmapped tests. Each question's `notes` says how many real failures fall outside its static set.
- **Unanswerable names.** These don't appear anywhere in the repo's files or SCIP symbols at the pinned commit.

## Known limitations

- **Static impact misses references through untyped values.** click's tests receive `runner` as an untyped pytest fixture, so SCIP can't resolve `runner.invoke(...).output`.
  - Of click's real failing tests, almost none are in the static sets. For example, `Result.output` has 980 real failures, and 938 of them are outside its 35-entry static set.
  - remeda's TypeScript tests are typed, and its static sets cover nearly all real failures (all but 5 of 57).
  - So static and real impact measure different things, and later scoring should report them separately.
- SCIP also misses dynamic references: `getattr`, string dispatch, and `obj[key] = …` going through `__setitem__`. Callers of overrides are listed separately in `notes`, but dynamic callers can still be missing from an answer.
- Any non-import reference counts, including references in type positions (a parameter annotated with the target class, a TypeScript overload signature). That matches the PRD's definition of a caller, but it's broader than "calls".
- httpx's test references are partly mis-resolved by the indexer (see above). Its questions avoid the affected names, but its ground truth still sees fewer test references than the code has.
- Module-level references have no caller.
- Static impact stops at depth 3, so its sets can be large (up to about 1,000 definitions for zod's core).
- requests and zod have no real impact data. The sandbox didn't mutate them because their suites take 81 s and 150 s per run.
- The outside-the-code unanswerable questions come from four templates, so their wording repeats across repos.

## Flow questions

AI agents drafted all 20 by reading only each repo's source at the pinned commit. Then a review applied one rule: must-cite lists hold only symbols that make a decision or transform data on the path, and pass-throughs move to the outline. That removed 11 symbols.
- The owner reviewed click, requests, and httpx question by question.
- For zod and remeda, the owner delegated the review. Claude checked each symbol against the code and applied the same rule. Their files say so in `Reviewed by:`.

## Audit

A seeded 10% sample of generated questions (11 of 110, `--seed 1`) was checked against the code at the pinned commits. Each mismatch was fixed in the generator, the benchmark was regenerated, and the sample was audited again. The sample is drawn by question ID, so each round checked the regenerated questions under the same IDs.

| Round | Found | Generator fix |
|---|---|---|
| 1 | zod's object-literal methods had no SCIP scope, so references inside them had no caller (`ZodString` calling `_jwt` was missing). | Fall back to the innermost parsed function, method, class, or test around the reference. |
| 2 | httpx: scip-python resolved `httpx.Cookies` to `Request` and `more_cookies.set` to the builtin `set`, so a test calling `Cookies.set` was missing and false edges appeared. Measured: 20% of httpx's references to ground-truth symbols are mis-resolved. | Drop references whose text doesn't name the target, and don't sample targets touched by a mis-resolution (above). |
| 2 | httpx: a call inside a `@base_url.setter` was attributed to the class `BaseClient`, because SCIP has no definition for the setter. | A parsed callable strictly inside SCIP's scope is the caller. |
| 2 | zod: `T extends schemas.$ZodBigInt` counted as a call, because the interface and the const share one SCIP symbol. | Exclude TypeScript targets that are also declared as an interface or type. |
| 2 | httpx: a tests-reach path went through `Response.is_redirect`, a member of a mis-resolved class. | Tests-reach intermediates must be trusted too. |
| 3 | All 11 match the code. The two click impact questions hit the untyped-fixture limitation above (their static sets are what SCIP can see). | none |

The audit was done by Claude, because the owner delegated it. Verdicts are in [`audit/audit-1.0.0.md`](audit/audit-1.0.0.md).

## Version history

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-12 | First version: 130 questions over 5 repos. The test split is frozen; questions sha256 `c121bf8d5914228d4998dc96e9e5ea675ac1b356591a2bc18f2a34c4fc8a9577`. |
