# 0024 — M16 without spend: incremental builds in full, cost work as a build gate

**Status:** Accepted · **Date:** 2026-11-15

## Context

M16 has two halves. Incremental builds and the freshness check call no model and are built and
measured in full. The cost half was planned around live measurements: cost and latency per
answering mode before any change, cheaper settings tried on the dev split, and a before/after
report with accuracy inside a tolerance. Decision 0020 says the build spends nothing, so those
runs can't happen now. This record applies the replan M12 to M15 used to the cost half, and
settles the design of the incremental build, which the milestone file left as rules to be
checked.

### What a build cost before this work (measured, no model involved)

Stage timings of the snapshots in the development database, colinhacks/zod (about 500 files,
5,200 nodes, 16,500 edges, 5,200 chunks), in seconds:

| Stage | First build | A later commit, 454 chunks new | Every embedding cached |
|---|---|---|---|
| fetch | 5.2 | 3.6 | 3.6 |
| parse | 3.2 | 2.7 | 2.6 |
| graph | 1.2 | 0.8 | 0.8 |
| store | 1.0 | 1.2 | 1.2 |
| chunk | 3.0 | 2.2 | 2.2 |
| embed | 1,842.9 | 484.9 | 1.8 |

Inside the graph stage (0.70 s for zod's 44,622 call sites): call resolution 0.27 s, `contains`
edges 0.23 s, import bindings 0.13 s, everything else under 0.1 s.

Two things follow. Embedding is the whole cost of a first build, and the `embeddings` cache
(M06) already removes it for every chunk that did not change. After that, parsing and chunking
are the largest parts a rebuild can avoid, and re-linking the graph is small.

## Decision

### Incremental builds

1. **Parse results are cached by content** in a new table, `parse_cache`, keyed by a digest of
   the file's path, blob OID, language, test flag, and the parser component versions (the
   inputs of `parse_file`). The `parse` stage looks every indexed file up, parses only the
   misses, and stores them. An unchanged file is a hit whatever snapshot it was parsed for, so
   this needs no previous snapshot. A cache must never fail a build: if the table can't be
   read, files are parsed.
2. **A build finds its previous snapshot itself:** the newest ready snapshot of the same
   repository and config fingerprint (another commit). `build_incremental(previous_snapshot_id,
   new_sha)` names one explicitly. With none, the build is a full build, as before.
3. **Change detection** compares the new inventory with the previous snapshot's `files` rows by
   path and blob OID: unchanged, modified, added, deleted, renamed (a deleted and an added path
   with the same OID; for everything built from a file a rename is a delete and an add, because
   symbol keys contain the path). It is recorded in the snapshot's stats.
4. **Chunks of unchanged files are copied** from the previous snapshot (same path and blob;
   node IDs remapped by symbol key; a file whose nodes can't all be found again is cut
   instead). Copied and newly cut chunks are inserted together in path order, so chunk IDs
   rise in the order a full build gives them: search breaks ties by chunk ID, and an
   incremental snapshot must answer like a full one. Embeddings were already reused by
   (chunk hash, model); the `embed` stage's `cached` and `embedded` counts are the evidence.
5. **The graph is linked again from the cached parse results, in full, by default.** Reusing
   call resolution per file is built, tested, and **opt-in** (`REPOATLAS_INCREMENTAL_LINK`),
   because it was measured slower; see below.
6. **The result must equal a full rebuild.** Scripted change sets (the milestone's list, and
   one per invalidation rule) compare the incremental graph's digest and its chunks with a
   full build of the same commit, through the store, with call reuse off and on; a randomized
   test applies generated edits in chains; `backend/scripts/incremental_check.py` does the
   same on pairs of real commits of real repositories.

The stage versions are unchanged, so the config fingerprint is unchanged and existing
snapshots stay valid.

### Reusing call resolution: built, correct, and off by default

The plan asked for re-resolving only changed files and their reverse-import dependents. That is
built as `link_graph(…, previous=…)`: the cheap whole-repository passes always run, and a
file's `calls` edges, unresolved calls, and call-site counts are kept from the previous
snapshot unless a rule invalidates the file.

**The planned rules were not enough to keep the graph equal.** They missed three ways a file's
calls can change without the file or its direct imports changing: a method removed two imports
away in an inheritance chain; a member added anywhere that name matching (tiers T5 and T6) now
finds; a file added or removed that changes which file an import reaches. The rules as built:

- `changed`: the file is modified or added;
- `imports`: its import statements resolve differently than in the previous build. This is
  checked exactly, by comparing a digest of the file's resolved imports and bindings with the
  one stored for it (`file_records`), so it covers an added file that an unresolved import now
  reaches, a deleted or renamed target, a shadowing file, and changed project configuration;
- `dependency`: any file it can reach through imports and bindings, at any depth, is
  modified, added, or has imports that resolve differently, or a path appeared in or vanished
  from a package it can reach (Python resolves `pkg.sub.f` by path);
- `name_match`: one of its call sites has the name of a symbol defined in a file of the kinds
  above, or in the previous version of a modified or deleted file.

Each rule has a scenario that gives a wrong graph when the rule is switched off. A stored edge
whose target no longer exists is never restored, whatever the rules say.

**It does not pay.** Sound rules invalidate most files on real change sets (zod across a
release: 468 of 503 files; flask across 40 commits: 78 of 83), and where they don't, the
stored calls have to be read back from the database, which costs more than resolving 44,622
call sites again from parse results already in memory. Measured on consecutive real zod
commits with every other reuse on, the graph stage took 1.1 s with call reuse, in the best
case for it (every file kept), against 1.0 s without it and 0.8 s in a full build. So the
default is to resolve every file's calls. The option stays because it is correct and tested, and because the plan asked
for it; `REPOATLAS_INCREMENTAL_VERIFY=true` additionally links in full and fails the build on
a difference. Per-file records are stored either way (they are small), so turning the option
on needs no rebuild.

### Freshness

`GET /snapshots/{id}/freshness` returns `snapshot_commit`, `remote_head` (from `git ls-remote`
on the default branch, cached for 5 minutes per repository), `stale`, `commits_behind` (from
GitHub's compare endpoint, asked only when stale; `null` when it can't be told, in which case
the banner says the map is behind without a number), `default_branch`, and `checked_at`. It
answers for a snapshot in any status. The banner's button sends the repository to
`POST /repos`, which pins the new head and queues a build; the build is incremental by rule 2.

### Cost work

1. **The product is unchanged by the constraint.** Routing, step caps, and evidence budgets
   stay at their current defaults. No cheaper setting is adopted without the measurement the
   tolerance rule needs.
2. **Prompt caching is extended, because it cannot change an answer.** A request now carries
   up to three breakpoints: the last tool definition, the last system block (as before), and,
   for a call that may use tools (tools given, tool choice `auto`), the last block of its last
   message, so each step of an exploration reads the steps before it from the cache instead of
   paying for them again. The marker is set on the request's copy; text content is sent as
   text blocks in every message of such a request, so a message has the same form in the
   request that marks it and in the later ones that resend it. Calls whose tail is never sent
   again are not marked: a one-shot answer, a repair turn, an agent's final call (another
   model), a branch's notes call. `REPOATLAS_LLM_CACHE_CONVERSATION=false` turns the third
   breakpoint off. Request shapes are tested for every mode through the real client over the
   offline stand-in, and the hand-written agent cassette was regenerated for the new shape;
   whether the API reports cache reads for them is a live-pass item
   (`tests/live/test_prompt_cache_live.py`).
3. **The tolerance rule is code.** `repoatlas eval cost-report --before … --after …` pairs the
   reports by system and split, prints cost and latency per question before and after (per
   question type and overall), the paired accuracy difference with its 95% interval, and a
   verdict per system: a change is kept only if, for every question type and overall, the
   interval's lower bound is above −0.02 (two points). A pair is `undecided` when the rule
   can't be applied honestly (nothing to pair, a report that stopped at its cost cap,
   different benchmark versions). With only `--before` it prints the baseline tables. Reports
   now keep each run's token counts, so the share of input read from the cache shows.
4. **The M16 gate is a build gate** for this half: the report tool is tested on stand-in
   reports of all three answering modes run through the eval runner (their numbers measure
   scripts and are not results), and a baseline is written from the reports that already
   exist (the M10 runs, measured live on 2026-11-12 with the prompt of that day), labelled as
   such. `repoatlas eval cache-estimate` prints what conversation caching saves by arithmetic
   on the price table, labelled an estimate.
5. **The live checks move to the live pass,** pending in [`docs/live-pass.md`](../live-pass.md)
   and in M16's file, never passed: cost and latency per mode before any change; cache reads
   confirmed in traces; cheaper settings tried on dev under the tolerance rule; the
   before/after report.

### Contract additions in earlier milestones

All are backward compatible; the milestone files list them under "Added later by decision
0024".

- **Graph building (M02).** `build_graph` is unchanged. `repoatlas.graph.build.link_graph`
  returns the graph with its per-file records and accepts a previous build to reuse call
  results from.
- **Store (M03).** Tables `parse_cache` and `file_records` (migration `0009`);
  `save_snapshot(…, records=…)`; `repoatlas.store.previous` and `repoatlas.store.parsecache`.
- **Builds and API (M04).** `BuildContext` gains `previous_snapshot_id`, `changes`, and
  `records`; the build job and `run_build` find the previous snapshot; `build_incremental`. A
  snapshot's stats gain `parse_cache`, `link`, and `incremental`. The freshness endpoint.
  Settings `parse_cache`, `incremental_builds`, `incremental_link`, `incremental_verify`.
- **Web app (M05).** `getFreshness`, the banner, and a stale snapshot in mock mode.
- **Retrieval stages (M06).** `chunk` copies the chunks of unchanged files; its stats are
  counted from the rows and gain `reused`, `rebuilt`, and `files_reused`.
- **LLM client (M07).** `build_request(…, cache_conversation=…)`; breakpoints as above;
  setting `llm_cache_conversation`.
- **Eval tooling (M10).** `repoatlas eval cost-report` and `cache-estimate`; token counts in
  `SystemOutput` and in a report's runs.

## Consequences

- A rebuild after a small change parses, chunks, and embeds only what changed. What every
  build still pays: fetching the commit, linking the graph, and writing the new snapshot's
  rows. The measured effect is in M16's Results and in `docs/performance.md`.
- One requirement is met in a stricter form than written. PRF-2 says changed files and their
  reverse-import dependents are re-resolved; by default every file is re-resolved (a superset,
  from cached parse results), because that is faster and cannot be wrong. The narrower
  behaviour is one setting away and gives the same graph.
- The cost half of M16 closes without numbers of its own. What prompt caching saves, and
  whether any cheaper setting survives the tolerance rule, is unknown until the live pass. The
  estimate says conversation caching saves nothing on a two-call exploration (it costs about
  7% more there) and 40% to 70% of the exploring calls' input cost from six calls on; those
  calls run on the small model, so the dollars are small either way.
- `parse_cache` grows with every distinct file version ever built; cleaning it up belongs
  with blob cleanup (backlog).
