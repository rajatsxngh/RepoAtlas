# Performance and cost

How RepoAtlas avoids repeating work when a repository changes, how it tells you a map is out of
date, and what is in place to make questions cheaper. The decisions behind it are in
[decision 0024](decisions/0024-m16-incremental-builds-cost-build-gate.md).

Numbers on this page are of two kinds, and each table says which: **measured** on this
project's development machine (Windows 11, CPU only, Postgres in Docker), or an **estimate**
by arithmetic. Nothing here was measured against a model: the cost of answers per mode is
pending in [live-pass.md](live-pass.md), items 20 to 23.

## Rebuilding a repository that changed

A build of a commit never starts from nothing if the store has seen the repository before.
Five things are reused, each by a key that says exactly what it depends on:

| What | Reused when | Kept in |
|---|---|---|
| File text | the blob is already stored (same git blob OID) | `blobs` |
| Parse results | same path, blob, language, test flag, and parser versions | `parse_cache` |
| Call resolution of a file (opt-in, see below) | no invalidation rule touches the file | the previous snapshot's `edges`, `unresolved_calls`, and `file_records` |
| Chunks of a file | same path and blob as in the previous snapshot | the previous snapshot's `chunks` |
| Embeddings | same chunk text and model (chunk hash) | `embeddings` |

"The previous snapshot" is the newest ready snapshot of the same repository built with the
same pipeline configuration (config fingerprint). A build finds it by itself; nothing has to
be asked for. `repoatlas.pipeline.incremental.build_incremental(previous_snapshot_id, new_sha)`
names one explicitly.

What is always done again: fetching the commit, listing its files, linking the graph from the
parse results (about a second for 500 files), and writing the new snapshot's rows.

### The result equals a full rebuild

A snapshot built on a previous one must be the snapshot a build from nothing would give. That
is checked at three levels:

- **Scripted change sets** (`tests/unit/pipeline/test_incremental.py`, and through the store in
  `tests/integration/pipeline/test_incremental_build.py`): edit a function body, add, delete,
  and rename a file, change an import, add a class that a name-matched call now finds, and
  more. The incremental graph's digest, the chunks with their keyword index, and their order
  must equal a full build's.
- **Generated edits** (`test_incremental_random.py`): 160 chains of four crude random edits
  each on four repositories, every step linked on top of the incremental result before it.
- **Real commits** (`backend/scripts/incremental_check.py`): pairs of real commits of real
  repositories, fetched from GitHub. Last run: seven pairs across six repositories, all equal
  (the output is in [m16-gate-evidence.md](m16-gate-evidence.md)).

With call resolution reused (next section), `REPOATLAS_INCREMENTAL_VERIFY=true` makes a build
also link the graph in full and fail if the digests differ.

### Reusing call resolution (off by default)

The linker can keep a file's `calls` edges, unresolved calls, and call-site counts from the
previous snapshot instead of resolving the file's calls again. It gives the same graph, and
it is **off by default because it was measured slower**: the stored calls have to be read
back from the database, which costs more than resolving them from parse results that are
already in memory (0.3 s more on zod even when every file is reused; see the measurements).
`REPOATLAS_INCREMENTAL_LINK=true` turns it on.

Deciding which files can keep their calls is the hard part, and the reason the rules are
written down and tested. Call resolution reads more than the file itself: the files it
imports, their classes' bases, and, for a call on an unknown receiver, every class in the
repository with a method of that name. A file keeps its stored calls only if none of these can
have changed:

| Rule | The file's calls are resolved again when |
|---|---|
| `changed` | the file is modified or added |
| `imports` | its import statements resolve differently than in the previous build (compared exactly, by a digest stored per file) |
| `dependency` | a file it can reach through imports and bindings, at any depth, is modified, added, or resolves its imports differently; or a path appeared in or vanished from a package it can reach |
| `name_match` | one of its call sites has the name of a symbol defined in a file of the kinds above, or in the old version of a modified or deleted file |

Every rule has a test that goes wrong without it. The rules are deliberately safe rather than
tight: an edit to a file that much of the repository imports invalidates most files (zod
across a release: 468 of 503 files; across five commits: 12).

### Measured

Stage timings of builds of colinhacks/zod (about 500 files, 5,200 nodes, 16,500 edges, 5,200
chunks), in seconds.

**What a first build costs, and what the embedding cache already saved before this work**
(from the snapshots in the development database):

| Stage | First build of the repository | A later commit, 454 chunks new | Every embedding cached |
|---|---|---|---|
| fetch | 5.2 | 3.6 | 3.6 |
| parse | 3.2 | 2.7 | 2.6 |
| graph | 1.2 | 0.8 | 0.8 |
| store | 1.0 | 1.2 | 1.2 |
| chunk | 3.0 | 2.2 | 2.2 |
| embed | 1,842.9 | 484.9 | 1.8 |

Embedding on a CPU is the whole cost of a first build, and reusing embeddings by chunk hash
(in place since the retrieval milestone) is what makes any rebuild fast.

**Incremental against full, same commits, embeddings warm for both**
(`REPOATLAS_INCREMENTAL_BUILDS=false REPOATLAS_PARSE_CACHE=false` for the full builds; medians
of three consecutive real commits, each changing one to three source files; seconds):

| Stage | Full rebuild | Incremental | |
|---|---|---|---|
| resolve | 0.5 | 0.5 | GitHub: repository checks and the pinned head |
| fetch | 4.5 | 5.0 | network; the same work either way |
| inventory | 0.3 | 0.2 | |
| parse | 2.6 | 0.5 | parse cache: 497 or 498 of 498 files were hits |
| graph | 0.8 | 1.0 | linked in full from the cached parse results |
| store | 1.0 | 0.9 | every row of the new snapshot is written |
| chunk | 2.2 | 0.8 | all but 1 to 3 files' chunks copied |
| embed | 1.0 | 1.0 | nothing new to embed on either side |
| **all stages** | **13.4** | **9.9** | about a quarter less |

What these numbers say, plainly:

- The saving is real and modest: about 3.5 of 13 seconds, from parsing and chunking. Fetching
  the commit and writing the snapshot are half of what is left and are paid by every build.
- A change that touches more code saves the same on parsing and chunking of the files it did
  not touch, and then pays for embedding its new chunks, at about one chunk per second on this
  CPU: 61 new chunks took 32 s (most of it loading the model), 454 took 485 s. That, not the
  seconds above, is what a user waits for after a large change.
- With call resolution reused too (`REPOATLAS_INCREMENTAL_LINK=true`), the graph stage took
  1.1 s instead of 0.8 to 1.0 s on three other commits, with every file's calls kept: slower,
  so it is off.
- Timings on this machine move by about 0.2 s per stage from run to run (the fetch by
  seconds); the table is medians, and differences smaller than that mean nothing.

## Is this map up to date?

`GET /api/v1/snapshots/{id}/freshness` compares the snapshot's commit with the head of the
repository's default branch:

```json
{
  "snapshot_commit": "c7ec94d309ee8acb25113e3db738b3e03816a424",
  "remote_head": "fef16cba0cf6668eb25ecc32fdfe2ce0a46f6f80",
  "stale": true,
  "commits_behind": 139,
  "default_branch": "main",
  "checked_at": "2026-11-15T06:49:39Z"
}
```

(That is the real answer for the zod snapshot in the development database on 2026-11-15.)

- The head comes from `git ls-remote` on the repository: one small request, no clone, no GitHub
  API quota. The answer is reused for five minutes per repository and branch.
- `commits_behind` comes from GitHub's compare endpoint and is asked only when the snapshot is
  stale. It is `null` when GitHub doesn't say (rate limit, a rewritten history); the banner then
  says the map is behind without a number.
- When the repository can't be reached the endpoint answers 502 and the web app shows nothing.

The map shows a banner on a stale snapshot ("This map is 139 commits behind `main`") with a
**Rebuild** button. The button sends the repository's URL to `POST /repos`, which pins the new
head and queues a build; the banner follows the build and opens the new map. The old snapshot
stays as it was.

## Cost of answers

### Prompt caching

Every request marks its static prefix for the prompt cache, and a tool conversation also marks
its growing tail (`repoatlas.llm.compat`):

| Breakpoint | On | Why |
|---|---|---|
| The last tool definition | every request with tools | requests that share tools but not the system prompt still share that much |
| The last system block | every request | tools and system prompt together are the static prefix of a mode |
| The last block of the last message | calls that may use tools (an agent's or a branch's exploring calls) | the next step resends this one's whole conversation and reads it from the cache |

Calls whose tail is never sent again carry no third breakpoint: a one-shot answer (its
evidence is unique to the question), a repair turn, an agent's final call (another model, and
caches are per model), a branch's notes call. Writing a cache entry costs 1.25 times the input
price, so marking something nobody reads would cost money.

`REPOATLAS_LLM_CACHE_CONVERSATION=false` turns the third breakpoint off.

Caching never changes an answer, so it needs no accuracy check. What it saves has not been
measured; this is what arithmetic on the price table says for the exploring calls of one agent
answer (`uv run repoatlas eval cache-estimate`):

**Estimate, not a measurement.** Static prefix of about 2,550 tokens (the agent's tools and
system prompt, counted as characters / 4), about 1,500 tokens added per step, on
`claude-haiku-5-5` ($0.10 per million input tokens, $0.01 read from the cache, $0.125 written
to it):

| Exploring calls | Prefix cached only | Conversation cached too | Saved |
|---|---|---|---|
| 2 | $0.00050 | $0.00054 | −7% |
| 4 | $0.00132 | $0.00101 | 23% |
| 6 | $0.00273 | $0.00154 | 43% |
| 8 | $0.00474 | $0.00214 | 55% |
| 12 | $0.01056 | $0.00350 | 67% |

Two things to read off it. An exploration of two calls pays slightly more than before (the
write is never read back; noted in the backlog). And the amounts are small: exploring runs on
the cheap model, and an agent answer's cost is mostly its final call on the large model, which
this does not touch. Live-pass item 21 checks that the API reports the cache reads at all;
item 20's reports will show the real share.

### The tolerance rule and the cost report

A cheaper setting (fewer steps, a smaller evidence budget, a lower effort) can cost accuracy,
so none is adopted without a measurement:

> A change is kept only if, for every question type and overall, the lower bound of the 95%
> interval of the paired difference in the primary metric is above −0.02 (two points).

`repoatlas eval cost-report` applies it:

```bash
cd backend
# What a set of reports cost: cost and latency per question, score, stage costs, cache reads.
uv run repoatlas eval cost-report --before ../eval/reports/<report>.json
# Before and after a change: per mode, the paired difference and the verdict.
uv run repoatlas eval cost-report --before <before reports or a folder> --after <after …> --out cost.md
```

It pairs reports by system and split, compares cost and latency on the questions both scored,
and prints `kept`, `rejected`, or `undecided` per mode (undecided: nothing to pair, a report
that stopped at its cost cap, or different benchmark versions). It exits with 4 when a change
is rejected. It reads reports and calls no model.

### What has been measured so far

[`eval/reports/2026-11-15-cost-baseline.md`](../eval/reports/2026-11-15-cost-baseline.md) is
the cost report of the runs made live on 2026-11-12, before the prompt was hardened and before
the cache breakpoints above:

| System (test split, 3 runs, 90 questions) | Cost per question | Latency, mean | Score [95% CI] |
|---|---|---|---|
| `hybrid_graphrag` (the product's one-shot retrieval) | $0.0220 | 4.9 s | 0.570 [0.492, 0.648] |
| `vector_only` | $0.0162 | 4.7 s | 0.522 [0.439, 0.608] |
| `bm25_only` | $0.0197 | 4.3 s | 0.441 [0.356, 0.528] |
| `closed_book` | $0.0036 | 3.2 s | 0.220 [0.150, 0.296] |

Agent and multi-agent answers have never been run against a model. Their baseline, and
one-shot answers on the current prompt, are live-pass item 20.

### Settings worth trying on dev (not adopted)

For the live pass, one at a time, each compared with the dev baseline of its mode under the
rule above ([live-pass.md](live-pass.md), item 22):

| Setting | Default | A cheaper value to try | What it trades |
|---|---|---|---|
| `REPOATLAS_AGENT_MAX_STEPS` | 12 | 8 | Fewer exploring calls; multi-hop questions may stop early |
| `REPOATLAS_AGENT_PER_TOOL_RESULT_TOKENS` | 2,000 | 1,200 | Smaller tool results in every later call; longer files get cut |
| `REPOATLAS_ANSWER_EVIDENCE_TOKENS` | 12,000 | 8,000 | Less evidence in one-shot answers and for the pipeline's writer |
| `REPOATLAS_MULTIAGENT_TOTAL_STEPS` | 24 | 16 | Fewer steps across all branches |
| `REPOATLAS_LLM_LARGE_EFFORT` | `medium` | `low` | Less thinking on final answers, the writer, and the verifier |

## Settings

| Variable | Default | What it does |
|---|---|---|
| `REPOATLAS_INCREMENTAL_BUILDS` | `true` | Reuse the previous snapshot of the repository (the chunks of unchanged files) |
| `REPOATLAS_INCREMENTAL_LINK` | `false` | Also reuse each file's call resolution from it (measured slower; same graph) |
| `REPOATLAS_PARSE_CACHE` | `true` | Reuse parse results across snapshots |
| `REPOATLAS_INCREMENTAL_VERIFY` | `false` | With `INCREMENTAL_LINK`: also link in full, and fail if the graphs differ |
| `REPOATLAS_LLM_CACHE_CONVERSATION` | `true` | The cache breakpoint on a tool conversation's last message |

None of them changes what a build or an answer contains, and none is part of the config
fingerprint.
