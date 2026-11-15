# M17 gate evidence

What was run for the M17 gate (decision
[0025](decisions/0025-m17-launch-build-gate-live-pass.md)) on 2026-11-16, on Windows 11 with
Postgres in Docker. **No model was called, nothing was deployed, nothing was published, and
nothing was spent.** This is a build gate: the launch itself is pending in
[live-pass.md](live-pass.md), items 24 to 30.

## The build path calls no model

Checked before the first precompute run. The stages a build runs are fetch, inventory, parse,
graph, store, chunk, embed, and publish. None of their packages imports the LLM client:

```bash
cd backend
grep -rnw "llm\|anthropic\|litellm" src/repoatlas/pipeline src/repoatlas/ingest \
  src/repoatlas/retrieval src/repoatlas/jobs src/repoatlas/store src/repoatlas/parsing \
  src/repoatlas/graph src/repoatlas/gallery --include=*.py
```

prints nothing. Embeddings come from the local model (decision 0009). The network calls a
build makes are to GitHub: repository metadata and `git fetch`.

## The ten gallery repos, precomputed on the local stack

```bash
cd backend
uv run repoatlas precompute --list ../launch/repos.yaml
```

First run (the five benchmark repos were already in the development database from M10, at
the same commits and configuration, so they were reused):

```text
ok      pallets/flask @ 49943b5a94: snapshot 54 (built, 588.5 s)
ok      colinhacks/zod @ c7ec94d309: snapshot 23 (reused, 0.4 s)
ok      psf/requests @ 6e83187b8f: snapshot 20 (reused, 0.4 s)
ok      pmndrs/zustand @ 9fed2eb2f3: snapshot 55 (built, 152.3 s)
ok      pallets/click @ cf19179e8e: snapshot 19 (reused, 0.6 s)
ok      encode/httpx @ b5addb64f0: snapshot 21 (reused, 0.3 s)
ok      remeda/remeda @ ec4d3f5dad: snapshot 22 (reused, 0.4 s)
ok      encode/starlette @ 271c277da0: snapshot 56 (built, 417.0 s)
ok      gvergnaud/ts-pattern @ f11be73acb: snapshot 57 (built, 309.3 s)
ok      pallets/itsdangerous @ 672971d66a: snapshot 58 (built, 37.8 s)
10 of 10 in the gallery.
```

Nearly all of a build's time is embedding on CPU (flask: 1,705 chunks in under ten minutes).
The run showed that `encode/starlette` now lives at `Kludex/starlette`; the list was
corrected, and the second run reused every snapshot (each in under a second) and ended with
`10 of 10 in the gallery.` again.

| # | Repository | Commit | License | Language | Files | Chunks | Benchmark |
|---|---|---|---|---|---|---|---|
| 1 | pallets/flask | `49943b5a94` | BSD-3-Clause | Python | 83 | 1,708 | |
| 2 | colinhacks/zod | `c7ec94d309` | MIT | TypeScript | 503 | 5,232 | yes |
| 3 | psf/requests | `6e83187b8f` | Apache-2.0 | Python | 37 | 841 | yes |
| 4 | pmndrs/zustand | `9fed2eb2f3` | MIT | TypeScript | 50 | 549 | |
| 5 | pallets/click | `cf19179e8e` | BSD-3-Clause | Python | 91 | 2,110 | yes |
| 6 | encode/httpx | `b5addb64f0` | BSD-3-Clause | Python | 60 | 1,301 | yes |
| 7 | remeda/remeda | `ec4d3f5dad` | MIT | TypeScript | 659 | 3,716 | yes |
| 8 | Kludex/starlette | `271c277da0` | BSD-3-Clause | Python | 84 | 2,244 | |
| 9 | gvergnaud/ts-pattern | `f11be73acb` | MIT | TypeScript | 88 | 922 | |
| 10 | pallets/itsdangerous | `672971d66a` | BSD-3-Clause | Python | 15 | 158 | |

Licenses are GitHub's SPDX identifiers, read on 2026-11-16. All ten are within the
production caps of `infra/production.env.example` (3,000 source files, 200 MB).

### Every card opens without a build

```bash
cd backend
uv run python scripts/gallery_check.py
```

It reads `GET /gallery` and, for each entry, the snapshot and the two reads of the map's
first screen, through the API in process on the development database:

```text
GET /gallery: 10 entries in 0.11 s
pallets/flask @ 49943b5a94 | snapshot 54 | ready | 4 top-level nodes | first open 0.18 s | again 7 ms | ok
colinhacks/zod @ c7ec94d309 | snapshot 23 | ready | 9 top-level nodes | first open 0.36 s | again 8 ms | ok
psf/requests @ 6e83187b8f | snapshot 20 | ready | 4 top-level nodes | first open 0.08 s | again 8 ms | ok
pmndrs/zustand @ 9fed2eb2f3 | snapshot 55 | ready | 6 top-level nodes | first open 0.06 s | again 7 ms | ok
pallets/click @ cf19179e8e | snapshot 19 | ready | 4 top-level nodes | first open 0.12 s | again 7 ms | ok
encode/httpx @ b5addb64f0 | snapshot 21 | ready | 2 top-level nodes | first open 0.14 s | again 8 ms | ok
remeda/remeda @ ec4d3f5dad | snapshot 22 | ready | 4 top-level nodes | first open 0.26 s | again 8 ms | ok
Kludex/starlette @ 271c277da0 | snapshot 56 | ready | 3 top-level nodes | first open 0.15 s | again 6 ms | ok
gvergnaud/ts-pattern @ f11be73acb | snapshot 57 | ready | 6 top-level nodes | first open 0.07 s | again 7 ms | ok
pallets/itsdangerous @ 672971d66a | snapshot 58 | ready | 3 top-level nodes | first open 0.05 s | again 8 ms | ok
```

"First open" loads the snapshot's graph into the API's memory (0.05 to 0.36 s, measured while
the test suite was using the machine); after that a read takes under 10 ms. In the browser:
the landing page of the local stack showed the ten cards, and the demo recording opens the
`psf/requests` card and reaches its map with no build screen.

Tests of the same path, which CI runs:

| What | Where |
|---|---|
| The list: ten entries, pinned, permissive, the benchmark's five at the benchmark's commits; unusable lists refused | `tests/unit/test_gallery_list.py` (22) |
| `precompute` through the real build on a local git origin: the pinned commit is built though the branch moved on; a second run fetches nothing; order, renames, pruning, `--only`, one entry failing; a snapshot that is not ready is not shown; the access gate covers the endpoint | `tests/integration/api/test_gallery.py` (8) |
| The command's `--dry-run`, a bad list, an unknown `--only` | `tests/unit/test_cli.py` (3) |
| The web app: cards link straight to `/s/{id}`; card text is text, never markup; nothing rendered for an empty or unreadable gallery | `Gallery.test.tsx` (4), `backend.test.ts` (1) |
| Playwright, real API and worker: the stack precomputes a fixture repo before the API starts; its card opens the map and no `POST /repos` is made | `e2e/app/gallery.spec.ts` (1) |
| Playwright, mock mode | `e2e/mock/gallery.spec.ts` (2) |

**Not shown:** the gallery on a hosted database, or how fast a card opens for a visitor over
the internet (live-pass item 24).

## The results page matches the reports

`docs/results.md` is written from the M10 and M11 test-split reports of 2026-11-12 and
2026-11-13. `tests/unit/test_results_page.py` (6) rebuilds its tables from the report files
(`eval/reports/*.json` and the comparison reports) and fails on any difference; it also fails
if agent or multi-agent mode is mentioned outside the "Pending" section. **The frozen test
split was not used in this milestone.** The final evaluation is live-pass item 25.

## README, demo, post

| What | Evidence |
|---|---|
| README numbers are the reports' | `tests/unit/test_readme.py` (4): the results table, the paired gain, the impact intervals; the status line says nothing is launched; the pending list names agent and multi-agent modes, the hardened prompt, injection, and the final evaluation |
| Links resolve | `python scripts/check_links.py`: `OK: 207 relative links in 68 files.` (in CI and pre-commit). `python scripts/check_links.py --external` on 2026-11-16: `OK: 230 relative and web links in 68 files, 1 unverified.` The unverified one is `https://deepwiki.com`, which answers automated clients with HTTP 429 |
| Demo script | `docs/launch/demo-script.md`: the 45-second plan, and what the existing recording covers |
| Demo recording | `docs/launch/demo.gif` (800 × 500, 2.8 MB), made by `frontend/scripts/record-demo.mjs` on the local stack: the gallery, the `psf/requests` card opening, folders opening, a search for `set_environ` (the target of benchmark question `requests-impact-003`), its code, and "What breaks?" with 37 affected symbols. The recorder watches for requests to the ask and explain endpoints and fails if it sees one; it saw none. `tests/unit/test_demo_assets.py` (3) |
| Post drafts | `docs/launch/linkedin-post.md` and `other-channels.md`. `tests/unit/test_launch_posts.py` (3): every interval in a draft is one of the measured ones; the URL is a placeholder; the drafts say agent and multi-agent modes are not evaluated |
| Go-public checklist and live-pass order | `docs/launch/go-public.md`; `docs/live-pass.md`. `tests/unit/test_go_public.py` (3): all 30 items are pending, the order names each of them exactly, and the Results section is still empty |

**Not shown, and nothing here claims it:** a public URL; caps verified in production; a
cited answer from a live model in the demo; a published post.

## Full checks

```bash
cd backend && uv run poe check
cd ../frontend && npm run lint && npm run typecheck && npm test && npx playwright test
python ../scripts/check_milestones.py
```

| Check | Result |
|---|---|
| `uv run poe check` | 2,591 passed, 1 skipped (symlinks need extra privileges on Windows), 9 deselected (the `live` tests) in 20 minutes; ruff, mypy (506 files), docs lint, and the link check pass |
| Frontend | Lint and typecheck clean; 351 unit tests in 42 files |
| Playwright, full run | 31 passed, 1 failed: `e2e/mock/multiagent.spec.ts:6` timed out waiting for a highlight on the map, in a run that stalled for 29 minutes on that test (the rest of the suite took 4). Nothing it covers was changed in this milestone |
| Playwright, that file again | `npx playwright test multiagent --project mock --repeat-each 3`: 9 of 9 passed, the failed test in 3.4 s each time |
| Docs lint | `OK: 18 milestones, 165 requirements, dependencies point backward only.` |

CI runs Playwright with one retry; its result is recorded in M17's Results.
