# M08 — Sandbox & benchmark environments

**Type of work:** Test infrastructure · **Size:** L (11 tasks) · **Status:** Done

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
- [x] **T10 — Target sampling.** SCIP-based, stratified, seeded; unit tests on a small SCIP fixture.
- [x] **T11 — Mutation runs.** At least 30 targets on at least 2 repos, confirmed failures, resumable; record counts, runtimes, and unmapped test IDs in Results.

## Exit criteria

- [x] Baseline runs are reproducible: a fourth run matches the non-quarantined outcomes of the first three for every pinned repo.
- [x] Mutation-engine unit tests pass.
- [x] At least 30 mutated functions per repo on at least 2 repos have confirmed failure sets in `mutations.jsonl`.
- [x] Runner tests prove: no network, no access to the Docker host, read-only root filesystem, limits enforced, timeout kill.
- [x] Every artifact is listed with its sha256 in `manifest.json`, and the manifest is committed.

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

Gate checked on 2026-11-12 on branch `m08-sandbox` (CI run 43145920458 passed on every job), Docker Desktop 29.9.2 (WSL2) on Windows 11.

**Tests.** In `backend/`: `uv run poe check` passes with Postgres and Docker up: 1,166 passed, 1 skipped (creating symlinks needs extra privileges on Windows), 7 deselected (the `live` tests). ruff, mypy (286 files) and the docs lint pass too. The 31 tests in `tests/integration/sandbox` run whenever Docker is available. They skip in CI unless `REPOATLAS_SANDBOX_TESTS=1`, because they build images.

**The five repos** (recipes in [`sandbox/recipes/`](../../sandbox/recipes/); artifacts in [`eval/artifacts/`](../../eval/artifacts/)):

| Repo | Language, size | Commit | License | Install pinning | Tests | Excluded | Suite time | SCIP |
|---|---|---|---|---|---|---|---|---|
| `pallets/click` 8.5.1 | Python, medium | `cf19179e8e2f` | BSD-3 | `uv.lock` | 2,459 | 27 pre-failing (pager tests need `less`) | 8 s | scip-python 0.6.8, 91 docs |
| `psf/requests` v2.34.2 | Python, small | `6e83187b8feb` | Apache-2.0 | `--exclude-newer` commit date | 633 | 4 pre-failing (connect timeouts, no network) | 81 s | scip-python 0.6.8, 34 docs |
| `encode/httpx` master | Python, medium | `b5addb64f016` | BSD-3 | pinned `requirements.txt` + `--exclude-newer` | 1,413 | 2 pre-failing | 8 s | scip-python 0.6.8, 60 docs |
| `colinhacks/zod` | TypeScript, large | `c7ec94d309ee` | MIT | `pnpm-lock.yaml`, pnpm 10.12.1 | 5,150 | 3 suite-level errors (`treeshake` needs a build) | 150 s | scip-typescript 0.6.2, 459 docs |
| `remeda/remeda` v2.59.0 | TypeScript, medium | `ec4d3f5dad11` | MIT | `package-lock.json` (`npm ci`) | 2,524 | none | 16 s | scip-typescript 0.6.2, 592 docs |

- zod is not on the M07 smoke commit (`19c40d2`). By then zod had moved to the `nub` package manager, whose lockfile pnpm rejects. `c7ec94d` (2026-08-29) is the last commit before the move.
- Base images: `node:22-bookworm-slim@sha256:18052396…`, `ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623…`, Python 3.12.13 managed by uv, and the indexers pinned by `package-lock.json`. Every image digest is in `image.json`.
- Docker disk: about 0.6 GB for the Python base, 0.4 GB for the Node base, and 0.1–0.6 GB per repo image. Downloads were about 1.5 GB.

**Exit criteria**
- *Baselines are reproducible.* Each repo ran its full suite 3 times (`repoatlas sandbox baseline --runs 3`), then a fourth time (`--check`). Every repo matched with 0 mismatches over all stable tests: click 2,459, requests 633, httpx 1,413, zod 5,150, remeda 2,524. No test was flaky, so nothing is quarantined for flakiness. Each recipe's `quarantine` holds its pre-failing tests, and `baseline.json` records per-run counts and the `check` block.
- *The mutation-engine unit tests pass.* `tests/unit/sandbox/test_mutate.py` has 24 tests. They cover decorated and async functions, methods, property setters by ordinal, generators (which stay generators), TS declarations, methods, field arrows, expression-bodied arrows, generators and overload implementations, and every skip rule (dunder, constructor, abstract, overload declaration, under 2 lines, not a function).
- *At least 30 mutated functions per repo on at least 2 repos have confirmed failure sets.* `repoatlas sandbox mutate --targets 36 --seed 7` ran on three repos. The sample is stratified by fan-in, and all four quartiles appear in each repo.

  | Repo | Reachable candidates | Mutated | With confirmed failures | Confirmed failing tests | Unmapped test IDs | Unconfirmed on re-run | Runtime (total, median per target) |
  |---|---|---|---|---|---|---|---|
  | click | 238 | 36 | 33 | 10,871 (median 62 per target) | 0 | 0 | 1,347 s, 19.7 s |
  | remeda | 347 | 36 | 36 | 1,434 (median 17) | 134 (9.3%) | 0 | 687 s, 18.6 s |
  | httpx | 185 | 36 | 36 | 3,470 (median 20.5) | 0 | 0 | 732 s, 13.2 s |

  - The 3 click targets without failures (`group`, `get_current_context`, a `confirmation_option` callback) run while test modules import. Their only failures were collection errors, which are excluded by design.
  - The 134 unmapped remeda failures are 74 distinct test IDs whose title paths `parse_file` builds differently from Vitest. Vitest titles `describe(pipe, …)` with the function's name, while `parse_file` drops that level. Table-driven titles (`.each`/`.for` rows) also don't match. They are kept with `symbol_key: null`; mapping them is an extractor change for later (see the backlog).
  - Candidates come from SCIP, as functions within 3 reference hops of a test file. requests (99 candidates) and zod (803) are indexed and ready, but weren't mutated: their suites take 81 s and 150 s per run.
- *Runner tests prove the isolation.* `tests/integration/sandbox/test_hardened.py` (11 tests) shows, in a real container:
  - only `lo` exists, and TCP to 1.1.1.1, `registry.npmjs.org`, `host.docker.internal:5433` (Postgres) and `:2375`, and `172.17.0.1` all fail;
  - there is no Docker socket, and all capabilities are 0 with `NoNewPrivs` set;
  - writes to `/src`, `/etc`, `/usr`, `/opt/venv` and the home folder fail, `/tmp` is capped at 512 MB, and cgroup `memory.max` is 4 GiB and `pids.max` 512;
  - a 512 MB allocation under a 128 MB limit is killed, a fork flood under `pids=16` is blocked, and `sleep 300` with a 3 s timeout is killed and its container removed.

  `test_proxy.py` shows that on the internal build network the three registries are reachable through the proxy. `example.com`, `github.com` and `host.docker.internal` get 403, and nothing is reachable without the proxy. `tests/unit/sandbox/test_hardened.py` pins the exact flags and that there are no mounts, privileges or port mappings.
- *Every artifact is listed with its sha256 in `manifest.json`, and the manifest is committed.* Each repo has a committed `manifest.json` covering `image.json`, `baseline.json`, `scip.json`, `index.scip`, and `mutations.jsonl` where present. It also holds the recipe hash and tool versions (repoatlas, Docker, base and repo image digests, upstream digests, the indexer version). `index.scip` (0.8–17 MB) is gitignored and marked `in_git: false`; rebuild it with `repoatlas sandbox scip`. `tests/unit/sandbox/test_artifacts.py` checks every committed manifest against the files and the current recipe.

**Notes for later milestones**
- **Mutation files in git.** `mutations.jsonl` is committed even though click's is 1.8 MB. `.pre-commit-config.yaml` exempts exactly these files from the 1 MB large-file check.
- **Proxy limits.** corepack's downloader ignores the proxy, so zod gets its pinned pnpm through npm. Vite and Vitest write caches into `node_modules`, so the TypeScript recipes point those folders at `/tmp`.
- **Commands.** Scripts reach containers on stdin, because Windows limits command-line length. Re-runs select Vitest files by absolute path.
- **requests indexing.** requests indexes a writable copy with a pyrightconfig that adds `tests` and `src`. Its pyproject limits pyright to the package, which hid every test reference.
- **zod's PATH.** The zod recipe's `PATH` env was added after its image was built. It only affects runs (it is not part of the install), so the recorded image and baseline still apply.
