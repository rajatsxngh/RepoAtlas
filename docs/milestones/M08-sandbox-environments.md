# M08 — Sandbox & benchmark environments

**Type of work:** Test infrastructure · **Size:** L (11 tasks) · **Status:** In progress

## Goal

Build safe, reproducible environments for a fixed set of benchmark repos, and use them to produce two kinds of ground truth that don't depend on RepoAtlas's own graph: compiler-grade code indexes (SCIP), and the real test failures caused by deliberately breaking individual functions.

## Depends on

- **M01** — `fetch_source` (bytes at a pinned commit, used to build the image's source layer), `parse_file` and symbol keys (to find function bodies to mutate and to key test cases), test-case extraction (pytest functions and classes, `describe`/`it` titles).
- **M00** — settings (`data_dir`), CLI, test markers, CI.

## Provides

- **`sandbox/` folder**
  - `images/python.Dockerfile`, `images/node.Dockerfile` — base images (pinned by digest) with a non-root user (uid 10001).
  - `proxy/` — an allowlist forward proxy (configuration for a small proxy such as Squid or tinyproxy) that permits only package registries (`pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`) and denies everything else.
  - `recipes/<repo_id>.yaml` — one recipe per benchmark repo.
- **Recipe schema** (`repoatlas.sandbox.recipe.Recipe`):
  ```yaml
  id: click
  url: https://github.com/pallets/click
  commit: <40-hex sha>
  language: python            # python | typescript
  base_image: python
  install: ["uv pip install --system -r requirements/tests.txt --exclude-newer 2026-06-01"]
  test:
    cmd: "pytest -q -p no:cacheprovider --junitxml=/tmp/out/junit.xml"
    reporter: junit           # junit | vitest-json | jest-json
    timeout_s: 900
  scip:
    indexer: scip-python      # scip-python | scip-typescript
    args: ["index", ".", "--project-name", "click"]
  quarantine: []              # test IDs, filled by baseline runs
  ```
- **`repoatlas.sandbox.runner.SandboxRunner`** (host side, Docker CLI through subprocess)
  - `build_image(recipe) -> ImageInfo(tag, digest)` — installs run on an internal network through the proxy.
  - `run_tests(image, *, mutation: Mutation | None, only: list[TestId] | None) -> TestRun(results, duration_s, exit_code, log_tail)`.
  - `run_scip(image) -> ScipArtifact(path, indexer, version, sha256)`.
  - All container security flags are applied in one function (`hardened_run_args`) and covered by tests.
- **`repoatlas.sandbox.results`** — parsers for JUnit XML and Vitest/Jest JSON → `TestResult(test_id, outcome: passed|failed|error|skipped, duration)`; `map_test_id(test_id, parsed_files) -> symbol_key | None`.
- **`repoatlas.sandbox.mutate`** — `mutate_python(source, symbol_key) -> str`, `mutate_ts(source, symbol_key) -> str`.
- **`repoatlas.sandbox.targets.sample_targets(scip_index, parsed, n, seed) -> list[SymbolKey]`**.
- **CLI:** `repoatlas sandbox build|baseline|scip|mutate <recipe> [--runs 3] [--targets N --seed S] [--resume]`.
- **Artifacts** under `eval/artifacts/<repo_id>/<commit>/` (large files gitignored; the manifest is committed):
  - `image.json` (image tags and digests, base image digests)
  - `baseline.json` (per-test outcomes for each run; quarantined and pre-failing sets)
  - `index.scip` and `scip.json` (indexer name, version, sha256)
  - `mutations.jsonl` (per target: symbol key, mutation kind, confirmed failing test IDs and their symbol keys, runtime)
  - `manifest.json` (sha256 of every artifact, recipe hash, tool versions)

## Requirements

SBX-1, SBX-2, SBX-3, SBX-4, SBX-5, SBX-6, SBX-7, SBX-8, SBX-9, SBX-10, SBX-11

## Scope

**In:** hardened runner, allowlist proxy and internal network, recipes, choosing and pinning five benchmark repos, baseline runs, SCIP indexing, mutation engine, target sampling, mutation runs, artifacts and manifests.

**Out:** generating benchmark questions, scoring any system, running user-submitted repos (never, in v1).

## Design notes

### Isolation
Test runs use:
```
--network none --cap-drop ALL --security-opt no-new-privileges
--user 10001:10001 --read-only --tmpfs /tmp:rw,size=512m,mode=1777
--pids-limit 512 --memory 4g --cpus 2 --ulimit nofile=4096:4096
```
- No bind mounts and no Docker socket. The repo source is baked into the image at build time (from `fetch_source`), not mounted.
- Suites that write to their working directory copy the source from the read-only layer into `/tmp/work` at container start.
- A host-side wall-clock timer kills the container after `timeout_s`.
- Docker Desktop on Windows runs containers in a shared WSL2 VM, so these flags are a defense layer, not a guarantee. That is why only curated benchmark repos ever run here.

### Installs
- Image builds run on an internal Docker network (no direct internet); the only route out is the allowlist proxy, passed as `HTTP_PROXY`/`HTTPS_PROXY`. The host (including `host.docker.internal` and the dev Postgres) is unreachable.
- Pinned installs: use the repo's lockfile when present (`uv.lock`, `poetry.lock`, hashed requirements, `package-lock.json` with `npm ci`, `pnpm`/`yarn` through corepack); otherwise resolve with `uv pip install --exclude-newer <commit date>` so dependency versions match the commit's era.
- Base image digests and built image digests are recorded.

### Choosing the five repos
- Criteria: permissive license; at least two Python and two TypeScript repos; one small, one medium, one large (within RepoAtlas's size caps); a test suite that runs inside the container in under ~15 minutes; not tiny toy projects.
- Candidates to evaluate (not decided): Python — `pallets/click`, `psf/requests`, `encode/httpx`; TypeScript — `colinhacks/zod`, `sindresorhus/ky`, `date-fns/date-fns`. The final list and commits go into Results and recipes.

### Baselines
- Run the full suite 3 times. A test whose outcome differs across runs is quarantined. A test that fails in all runs is pre-failing and excluded from ground truth. Both lists are written to the recipe's `quarantine` and to `baseline.json`.

### SCIP indexes
- `scip-python` and `scip-typescript` run inside the repo image (dependencies installed, network off during indexing), producing `index.scip`. Indexer versions are pinned in the base images.

### Mutations
- Python: parse with `ast`/`libcst`, locate the function by symbol key (via `parse_file`), replace its body with `raise RuntimeError("repoatlas-mutation")`, keeping the signature and decorators.
- TypeScript: locate with tree-sitter, replace the body with `{ throw new Error("repoatlas-mutation"); }`; arrow functions with expression bodies get a block body.
- Skipped targets: dunder methods, constructors (`__init__`, `constructor`), abstract methods, overload declarations, generators where replacement would not compile, functions under 2 lines.

### Target sampling
- From SCIP: functions defined in non-test files that test files reference directly, or within 3 reference hops.
- Stratify by fan-in quartile (number of distinct referencing definitions), sample with a fixed seed, at least 30 per repo for at least 2 repos (more if runtime allows).

### Mutation runs
- For each target: build a mutated layer on top of the repo image (source change only), run the full suite, collect failures.
- Confirm: re-run only the failing tests; keep tests that fail again. Exclude collection errors (pytest errors during collection, suite-level failures in Vitest/Jest), quarantined tests, and pre-failing tests.
- Map each failing test ID to a test symbol key: pytest node IDs `path::Class::test[param]` → strip parameters; Vitest/Jest `file > describe > it` → title path. Unmapped IDs are kept with `symbol_key = null` and counted.
- The loop is resumable: completed targets in `mutations.jsonl` are skipped.

## Tasks

- [x] **T1 — Recipe schema.** Pydantic model, loader, validation errors with line numbers.
- [x] **T2 — Proxy and internal network.** Proxy config and setup command; test that an allowlisted registry works and any other host (and the Docker host) is blocked.
- [x] **T3 — Base images and image builds.** Source baked in from `fetch_source`; digests recorded.
- [x] **T4 — Hardened runner.** `hardened_run_args`; tests that a container has no network, can't reach the host, has a read-only root, respects memory/pids limits, and is killed at the timeout.
- [x] **T5 — Result parsing and test mapping.** JUnit and Vitest/Jest JSON parsers; test ID → symbol key mapping with fixtures.
- [x] **T6 — Choose and pin five repos.** Apply the criteria; write recipes; confirm each suite runs in its container.
- [x] **T7 — Baselines.** Three runs per repo; quarantine and pre-failing lists; `baseline.json`.
- [x] **T8 — SCIP indexing.** Run indexers in the images; `index.scip`, `scip.json`; manifests with digests.
- [x] **T9 — Mutation engines.** Python and TypeScript, with unit tests (decorated functions, methods, arrow functions, skip rules).
- [ ] **T10 — Target sampling.** SCIP-based, stratified, seeded; unit tests on a small SCIP fixture.
- [ ] **T11 — Mutation runs.** At least 30 targets on at least 2 repos, confirmed failures, resumable; record counts, runtimes, and unmapped test IDs in Results.

## Exit criteria

- [ ] Baseline runs are reproducible: a fourth run matches the non-quarantined outcomes of the first three for every pinned repo.
- [ ] Mutation-engine unit tests pass.
- [ ] At least 30 mutated functions per repo on at least 2 repos have confirmed failure sets in `mutations.jsonl`.
- [ ] Runner tests prove: no network, no access to the Docker host, read-only root filesystem, limits enforced, timeout kill.
- [ ] Every artifact is listed with its sha256 in `manifest.json`, and the manifest is committed.

## Verification

```bash
cd backend
uv run pytest tests/integration/sandbox -q                    # needs Docker
uv run repoatlas sandbox build ../sandbox/recipes/click.yaml
uv run repoatlas sandbox baseline ../sandbox/recipes/click.yaml --runs 3
uv run repoatlas sandbox scip ../sandbox/recipes/click.yaml
uv run repoatlas sandbox mutate ../sandbox/recipes/click.yaml --targets 30 --seed 7
```

## Risks and open questions

- Some suites need services (databases, browsers) or network access; such repos fail the selection criteria.
- Mutation runs are slow on large suites; resumability and per-repo target counts keep this manageable.
- SCIP indexers may not support every project setup; a repo whose index is clearly incomplete is replaced.
- Open: the final five repos and their commits.

## Results

_Filled in at the gate._
