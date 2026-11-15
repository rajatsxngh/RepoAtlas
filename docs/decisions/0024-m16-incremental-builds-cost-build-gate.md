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

### What a build costs today (measured, no model involved)

Stage timings of the snapshots in the local database, colinhacks/zod (505 files, 5,185 nodes,
16,490 edges, 5,232 chunks), in seconds:

| Stage | First build | Rebuild with every embedding cached |
|---|---|---|
| resolve | 0.7 | 0.6 |
| fetch | 3.6 | 3.6 |
| inventory | 0.2 | 0.3 |
| parse | 2.7 | 2.6 |
| graph | 0.8 | 0.8 |
| store | 1.2 | 1.2 |
| chunk | 2.2 | 2.2 |
| embed | 484.9 (454 chunks embedded; 1,842.9 for all 5,320 the first time) | 1.8 |

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
   this needs no previous snapshot.
2. **A build finds its previous snapshot itself:** the newest ready snapshot of the same
   repository and config fingerprint (another commit). `build_incremental(previous_snapshot_id,
   new_sha)` names one explicitly. With none, or with one built before this milestone (it has
   no file records), the build is a full build, as before.
3. **Change detection** compares the new inventory with the previous snapshot's `files` rows by
   path and blob OID: unchanged, modified, added, deleted, renamed (a deleted and an added path
   with the same OID; for everything below a rename is a delete and an add, because symbol keys
   contain the path).
4. **What is linked again.** The graph builder's passes that read the whole repository and are
   cheap are run in full on every build: structure, import bindings and `imports` edges, class
   hierarchies and `inherits` edges, routes, test links, public markers, owners, `contains`
   edges, and the statistics. **Call resolution**, the pass that does work per call site, is
   reused per file from the previous snapshot's stored graph unless the file is invalidated.
5. **Invalidation rules.** The milestone's rules are kept and made sound; the planned ones
   missed three ways a file's calls can change without the file or its direct imports changing
   (a method removed two imports away in an inheritance chain; a member added anywhere that
   name matching now finds; a file added that shadows an import that used to resolve). A file's
   calls are resolved again when:
   - `changed`: the file is modified or added;
   - `imports`: its import statements resolve differently than in the previous build. This is
     checked exactly, by comparing a digest of the file's resolved imports and bindings with
     the one stored for it, so it covers an added file that an unresolved import now reaches,
     a deleted or renamed target, a shadowing file, and changed project configuration;
   - `dependency`: any file it can reach through imports and bindings, at any depth, is
     modified, added, or has imports that resolve differently, or a deleted path lies inside a
     package it can reach (Python resolves `pkg.sub.f` by path);
   - `name_match`: one of its call sites has the name of a symbol defined in a file of the
     three kinds above, or in the previous version of a modified or deleted file (name
     matching, tiers T5 and T6, looks members up across the whole repository).
   A file under none of these keeps its `calls` edges, unresolved calls, and call-site counts.
6. **Per-file records** (`file_records`: the import digest and the call-site counts of each
   file) are saved with every snapshot, so the next build can compare and can rebuild the
   statistics without resolving.
7. **Chunks of unchanged files are copied** from the previous snapshot (same path and blob,
   node IDs remapped by symbol key); only the other files are chunked. Embeddings were already
   reused by (chunk hash, model); the `embed` stage's `cached` and `embedded` counts are the
   evidence.
8. **The result must equal a full rebuild.** Scripted change sets on the fixture repositories
   (one per rule, and the milestone's list) compare the incremental graph's digest and its
   chunks with a full build of the same commit; a randomized test applies generated edits; a
   check on a real repository does the same across two real commits. Setting
   `REPOATLAS_INCREMENTAL_VERIFY=true` makes every incremental build also link the graph in
   full and fail on a difference (off by default: it costs what the reuse saves).

The stage versions are unchanged, so the config fingerprint is unchanged and existing
snapshots stay valid.

### Freshness

`GET /snapshots/{id}/freshness` returns `snapshot_commit`, `remote_head` (from `git ls-remote`
on the default branch, cached for 5 minutes per repository), `stale`, and `commits_behind`
(from GitHub's compare endpoint; `null` when it can't be told, in which case the banner says the
map is behind without a number). The banner's button sends the repository to `POST /repos`,
which pins the new head and queues a build; the build is incremental by rule 2.

### Cost work

1. **The product is unchanged by the constraint.** Routing, step caps, and evidence budgets
   stay at their current defaults. No cheaper setting is adopted without the measurement the
   tolerance rule needs.
2. **Prompt caching is extended, because it cannot change an answer.** A request now carries
   up to three breakpoints: the last tool definition, the last system block (as before), and,
   in a conversation with tool results, the last block of the last message, so each step of an
   agent's exploration reads the steps before it from the cache instead of paying for them
   again. `REPOATLAS_LLM_CACHE_CONVERSATION=false` turns the third off. Request shapes are
   tested for every mode with `FakeLLM` and the cassettes; whether the API reports cache reads
   for them is a live-pass item.
3. **The tolerance rule is code.** `repoatlas eval cost-report --before … --after …` pairs the
   reports by system, prints cost and latency per question before and after (per question type
   and overall), the paired accuracy difference with its 95% interval, and a verdict per
   system: a change is kept only if, for every question type and overall, the interval's lower
   bound is above −0.02 (two points). With only `--before` it prints the baseline tables.
4. **The M16 gate is a build gate** for this half: the report tool is tested on stand-in
   reports (their numbers measure scripts and are not results), and a baseline is written from
   the reports that already exist (the M10 runs, measured live on 2026-11-12 with the prompt of
   that day), labelled as such.
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
  `save_snapshot(…, records=…)`.
- **Builds (M04).** `BuildContext` gains `previous_snapshot_id`, `changes`, and `records`; the
  build job and `run_build` find the previous snapshot. A snapshot's stats gain `incremental`
  and `parse_cache`.
- **Retrieval stages (M06).** `chunk` copies the chunks of unchanged files; its stats gain
  `reused` and `rebuilt`.
- **LLM client (M07).** `build_request(…, cache_conversation=…)`; breakpoints as above.
- **Eval tooling (M10).** `repoatlas eval cost-report`.

## Consequences

- A rebuild after a small change parses, chunks, and embeds only what changed. On the measured
  repository that is about half of a warm full rebuild; the rest (fetching the commit and
  writing the new snapshot's rows) is paid by every build.
- Selective call resolution saves little by itself (0.27 s of zod's rebuild at most). It is
  built because the requirement asks for it and the rules are now sound, not because it is
  where the time goes; the Results section reports what each part saved.
- An edit to a file that much of the repository imports invalidates most files. That is the
  rule being correct, not a defect.
- The cost half of M16 closes without numbers of its own. What prompt caching saves, and
  whether any cheaper setting survives the tolerance rule, is unknown until the live pass.
