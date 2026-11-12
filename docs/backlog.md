# Backlog

Ideas that are deliberately **not** part of any milestone. Add new ideas here instead of growing the current milestone. Revisit after launch.

| Idea | Why it might matter | Notes |
|---|---|---|
| SCIP-powered product edges | Precise callers for repos whose dependencies can be installed | Needs a safe way to install dependencies for arbitrary repos; today SCIP runs only in the sandbox for curated repos |
| More languages (Go, Java, Rust) | Wider audience | Each needs extractors, resolvers, and benchmark coverage |
| Understand-Anything as an evaluation baseline | Compare an LLM-built graph with a code-built graph on the same questions | Needs a fair setup and its graph output for the benchmark repos |
| On-demand LLM summaries per node | Plain-English descriptions in the node panel | Generated when a node is opened, cached by blob OID; counted in cost metrics |
| Guided tours | Dependency-ordered walkthroughs for onboarding | Could reuse the agent tools |
| Community-detection containers | Better grouping for huge flat directories | Louvain or similar at build time |
| Private repos and authentication | Use on work code | Needs auth, token handling, and a security review |
| Multi-repo graphs | Microservice systems | Cross-repo symbol keys and resolution |
| Diff and pull-request upload UI, GitHub app | Impact analysis on real PRs | The engine already accepts unified diffs |
| Answer caching per snapshot | Lower cost for repeated questions | Measure demand first |
| Local cross-encoder reranker | Possibly better retrieval | Try behind the existing `Retriever` interface and evaluate on dev |
| Approximate vector index | Very large deployments | Per-model partial indexes; watch filtering skew |
| Small-model injection classifier | Better detection of novel injection phrasing | Only if pattern recall proves too low |
| Test-file-level impact predictions | Matches how people run tests | Report alongside test-function level |
| A sixth, held-out benchmark repo | Extra check for overfitting | Used once at the end |
| TypeScript variance annotations | Files with `in`/`out` type parameters (TypeScript 4.7+) report parse issues | tree-sitter-typescript 0.23.2, the newest release on PyPI in November 2026, can't parse them. On zod the errors stay inside the type-parameter lists and cost no symbols. Upgrade the grammar once a release supports them, then rerun `backend/scripts/smoke_parse.py` |
| py-tree-sitter 0.26 or later | Binding fixes and speedups | 0.26.0 crashed the interpreter mid-extraction on Windows; move the pin only after the smoke script and the full suite pass on the new release |
| Narrower wrapped-function detection | `const DATA = makeData(1000, () => …)` becomes a function symbol, though only wrappers like `memo(function …)` should | The TypeScript extractor treats any `const X = f(<function>)` as a wrapped function. Limiting it to known wrappers (or to calls whose result is a component) changes symbol kinds and the parse goldens |
| Local type inference for name-matched calls | T5/T6 call edges are weak on real repos (zod 0/5 and 1/5, flask 4/5 and 1/5 in the graph hand check) | Track receivers assigned from a constructor call (`cart = Cart()`), annotated parameters (`cart: Cart`), and `isinstance` checks, then resolve `cart.add()` like `self.add()` |
| Snapshot and blob cleanup | Deleting a snapshot leaves blobs that no snapshot uses, and old snapshots pile up | Delete blobs no `files` row references, in batches, under a retention policy for old snapshots; blobs are shared by OID, so never delete one another snapshot still uses |
| Cancel a build | Stop a long build from the web app | No endpoint yet: builds take seconds to minutes, and an operator can stop a worker gracefully (the job goes back to the queue). A `POST /jobs/{id}/cancel` would mark the job and let the worker's stage check end it |
| Rate limits shared across API instances | Several API processes behind a load balancer | Buckets live in one process's memory; more instances need shared counters (a Postgres table with an upsert per request is enough at this scale) |
| Orphaned build directories | A retry on another machine leaves the first machine's checkpoints behind | Checkpoints are removed when a build succeeds or fails for good on the machine that holds them; a worker could also sweep `data_dir/builds` for snapshots that are ready, failed, or gone |
| Symbol edges across expanded files | With two files open on the map, calls between their symbols still show as one file-to-file edge | The map lifts the root's file-level aggregate to visible nodes and takes symbol edges only from each open file's own subgraph. Drawing cross-file symbol edges needs a symbol-level aggregate for the pair (or `neighbors` per open symbol), within the edge budget |
| Faster first-build embeddings | Embedding zod's 5,645 chunks took 31 minutes on CPU (about 3 chunks per second) | Try a quantized or smaller code model, `embedding_threads`, or an optional GPU provider; compare quality on the dev split first, since the model enters the fingerprint |
| Refusal fallbacks | A safety classifier declines an ordinary code question (false positive), and the user gets an API refusal | The API's server-side `fallbacks` (beta) re-runs a declined request on another model. It is off because a silent fallback would hide refusals from the metrics, and LiteLLM's own `fallbacks` keyword collides with it; enabling it needs the fallback recorded on the span and in `qa_runs` |
| Vitest title paths for function-named suites and table rows | 74 of remeda's failing test IDs in the mutation ground truth have no test symbol key | Vitest titles `describe(fn, …)` with the function's name, and `.each`/`.for` rows with their formatted titles; the TypeScript extractor drops the first and doesn't emit the second. Changing it bumps `EXTRACTOR_VERSION` and the parse goldens |
| Mutation runs for requests and zod | Ground truth on all five repos, not three | Both are indexed and baselined (99 and 803 candidates), but their suites take 81 s and 150 s per run; about 1.5 h and 2.5 h for 36 targets each with `--resume` |
| `less` in the Python base image | click's 27 pager tests are pre-failing only because `less` is missing | Adding it changes the base image digest, so every Python image, baseline, and mutation run would be rebuilt |
