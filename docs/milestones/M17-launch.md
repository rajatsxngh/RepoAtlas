# M17 — Launch

**Type of work:** Release and content · **Size:** M (8 tasks) · **Status:** Done

## Goal

Make RepoAtlas public and tell the story once — with a working demo, precomputed repos that load instantly, and published numbers.

## Depends on

- **M00–M16 (all earlier milestones)** — in particular: the private deployment, limits, and runbook (M12); the guardrail gate results (M13); all answering modes and their reports (M10, M11, M14, M15); cost and incremental builds (M16).

## Provides

As built at the build gate ([decision 0025](../decisions/0025-m17-launch-build-gate-live-pass.md)). What the plan promised and the build could not deliver without spending is listed last.

- **Precomputed gallery:** `launch/repos.yaml` (10 well-known, permissively licensed repos at pinned commits, the five benchmark repos among them) and its loader (`repoatlas.gallery.listing`); `repoatlas precompute --list launch/repos.yaml [--only owner/name] [--dry-run]`; the `gallery` table (migration `0010_gallery`); `GET /gallery`; a gallery on the landing page whose cards link straight to ready snapshots.
- **Results page:** `docs/results.md`, written from the M10 and M11 test-split reports: tables with confidence intervals, cost per question, a "where RepoAtlas loses" section, known biases, and a table of what is not measured yet. `backend/tests/unit/test_results_page.py` compares it with the report files.
- **README:** a demo GIF at the top, the pitch, an architecture diagram, the results summary with intervals and cost per question, what is pending, the self-hosting guide, related projects and credits, and the license.
- **Demo:** the 45-second script in `docs/launch/demo-script.md`; `docs/launch/demo.gif`, recorded on the local stack with `frontend/scripts/record-demo.mjs`, showing only features that use no model.
- **Launch post drafts:** `docs/launch/linkedin-post.md` (the owner publishes it), with the source of every number, and optional drafts for other channels in `docs/launch/other-channels.md`.
- **Go-public readiness:** `scripts/check_links.py` (relative links and anchors in CI; `--external` for web links); the checklist `docs/launch/go-public.md`; the "Live pass order" in `docs/live-pass.md`.
- **Built for, not delivered (live pass):** the public deployment with caps verified in production (LCH-1); the final evaluation of every system on the frozen test split (LCH-3); the demo recorded on the public URL with a live cited answer (LCH-5); the published post; notes to maintainers (LCH-7).

## Requirements

LCH-1, LCH-2, LCH-3, LCH-4, LCH-5, LCH-6, LCH-7

## Scope

**In:** going public, precomputing and the gallery, final evaluation and results page, README, demo video, launch post drafts, post-launch checks.

**Out:** new features (they go to the backlog).

## Design notes

- **Gallery repos:** well-known, permissively licensed, within the production caps, a mix of Python and TypeScript, at least one that is in the benchmark (so the published numbers connect to something visitors can try).
- **Final evaluation:** the frozen test split is used exactly once more here for all systems at their final settings; every run is kept; results are reproducible from the report metadata (benchmark digest, config fingerprints, model IDs, commit).
- **Honesty:** the results page leads with the main comparison (hybrid GraphRAG vs vector-only on multi-hop; impact recall vs real failures), includes losses, and states known biases (same-family judge, popular-repo memorization measured by the closed-book baseline).
- **Launch post:** what it does, one striking visual, two or three numbers with intervals, what didn't work, credits to the projects that inspired it, a link to try it. Posted once, by the owner.
- **Maintainers:** optional, polite notes to maintainers of gallery repos with a link to their map.
- **After launch:** watch spend and errors daily for a week; fix critical bugs; everything else goes to the backlog.

## Tasks

Replanned on 2026-11-16 by [decision 0025](../decisions/0025-m17-launch-build-gate-live-pass.md): the build spends nothing, so the tasks that need live calls, hosting, or publishing are built up to that point, and the rest is pending in the live pass.

- [x] **T1 — Choose gallery repos.** Ten repos, pinned commits, `launch/repos.yaml`; a loader that rejects a list the gallery should not show (unpinned, duplicate, not permissively licensed).
- [x] **T2 — Precompute and gallery.** `repoatlas precompute`; the `gallery` table and `GET /gallery`; gallery UI; Playwright tests that gallery snapshots open without a build.
- [x] **T3 — Results page (from the reports that exist).** `docs/results.md` from the M10 and M11 test-split reports, with intervals, cost, losses, biases, and a table of what is pending; a test that the page's numbers are the reports'. The final evaluation is a live-pass item.
- [x] **T4 — README.** Demo GIF, architecture diagram, results, cost, self-hosting, credits; every number labelled measured or pending.
- [x] **T5 — Demo video (script and local recording).** The 45-second script; a recording on the local stack of the scenes that need no model. The full recording is a live-pass item.
- [x] **T6 — Launch post drafts.** LinkedIn post (and optional others) for the owner to publish.
- [x] **T7 — Go-public readiness.** `scripts/check_links.py` in CI; the go-public checklist; the live-pass order. Removing the gate and verifying production are live-pass items.
- [x] **T8 — Gate.** The ten repos precomputed on the local stack; gate evidence; backlog triage; Results filled in. Post-launch monitoring is a live-pass item.

## Exit criteria

- [x] All 10 gallery repos are precomputed on the local stack and open from the landing page without a build (the snapshot is ready before the click).
- [x] `docs/results.md` publishes the measured test-split results with intervals and cost, names what is pending, and matches the report files (checked by a test).
- [x] The README's links resolve (`python scripts/check_links.py`, and once with `--external`); the demo script is written and the local recording is in the README; the launch post is drafted.
- [x] The go-public checklist and the live-pass order exist, and every launch-time live item is listed as pending.

### Live pass (pending; not part of this gate)

These need spending, hosting, or publishing, which the owner has deferred or keeps (decisions 0020 and 0025). They are not checked here and must never be reported as passed until they are run:

- The gallery precomputed on the hosted database, opening from the public landing page.
- The final evaluation: every system on the frozen test split, three runs each, at its final settings; `docs/results.md` and the README updated from it.
- Going public: the access gate removed; spend caps and per-client caps verified in production; the public URL healthy.
- The 45-second demo recorded on the public URL, with a cited answer from the live model.
- The launch post published by the owner.
- One week of daily spend and error monitoring after launch.
- Optional (LCH-7): notes to maintainers of gallery repos.

## Verification

```bash
cd backend
uv run repoatlas precompute --list ../launch/repos.yaml
uv run pytest tests/unit/test_gallery_list.py tests/unit/test_results_page.py tests/integration/api/test_gallery.py -q
cd ../frontend && npx playwright test gallery
cd .. && python scripts/check_links.py
```

Live pass (needs an API key, hosting, and the owner): see [`docs/live-pass.md`](../live-pass.md), "Live pass order" and items 24 to 30.

## Risks and open questions

- A traffic spike after the post; precomputed repos, caps, and the spend guard protect costs.
- Final numbers may be less impressive than hoped; the post leads with what is true.
- Open: which gallery repos best show off "What breaks?".

## Results

**Build gate reached on 2026-11-16 (decision 0025). No model was called, nothing was deployed, nothing was published, and nothing was spent.** RepoAtlas is built and ready to launch; it is not launched. Evidence: [`docs/m17-gate-evidence.md`](../m17-gate-evidence.md). What remains is the owner's: [`docs/live-pass.md`](../live-pass.md), which now opens with a recommended order.
CI run 43765645211 passed all five jobs on `b637fdd` (Linux full checks, Windows unit tests, API types, frontend with end-to-end tests, docs lint with the link check).

What was built:

- **The gallery.** `launch/repos.yaml` pins ten permissively licensed repositories (six Python, four TypeScript; the five benchmark repositories at the benchmark's commits). `repoatlas precompute` builds each through the ordinary build and records it in the new `gallery` table; `GET /gallery` lists the entries whose snapshot is ready; the landing page shows them as cards that link straight to the map.
- **`docs/results.md`**, from the M10 and M11 test-split reports: the four-system table with intervals and cost, the paired comparison, impact against static and real ground truth, where RepoAtlas loses, known biases, and a table of what is not measured.
- **The README**, with the demo GIF, an architecture diagram, the results summary, what is pending, self-hosting, and credits.
- **The demo:** a 45-second script, and `docs/launch/demo.gif`, recorded on the local stack by `frontend/scripts/record-demo.mjs`, showing the gallery, a map, search, code, and "What breaks?".
- **Post drafts** for LinkedIn and, optionally, other channels, with the source of every number.
- **`scripts/check_links.py`** in CI, pre-commit, and `poe check`; the **go-public checklist**; and the **live pass order**.

Numbers of the gate:

| Check | Result |
|---|---|
| The ten gallery repos precomputed locally | Pass: `10 of 10 in the gallery.` Five were built in this milestone (flask 589 s, starlette 417 s, ts-pattern 309 s, zustand 152 s, itsdangerous 38 s, nearly all of it embedding on CPU); the five benchmark repos were reused from M10's snapshots |
| Every card opens without a build | Pass: `scripts/gallery_check.py` reads each entry's snapshot (ready) and first screen through the API; first open 0.05 to 0.36 s, then under 10 ms. Playwright against the real API and worker: a precomputed fixture opens from the landing page and no build is requested |
| The results page matches the reports | Pass: 6 tests rebuild its tables from the report files |
| README, post drafts, demo, checklist | Pass: 13 tests (numbers are the reports'; nothing pending is worded as done; the URL is a placeholder) |
| Links | Pass: 207 relative links in 68 files. With `--external` on 2026-11-16: every web link answered except `https://deepwiki.com`, which refuses automated clients (HTTP 429) and is reported as unverified |
| `uv run poe check` | Pass: 2,591 passed, 1 skipped, 9 deselected (the live tests); ruff, mypy (506 files), docs lint, link check |
| Frontend | Lint and typecheck clean; 351 unit tests |
| Playwright | 31 of 32 in the full run. The one failure, `e2e/mock/multiagent.spec.ts:6` (code this milestone did not touch), stalled for 29 minutes in a run that otherwise took 4; run again three times it passed every time (9 of 9 for its file). The three new gallery specs pass. In CI the frontend job, which runs the whole suite, passed |

What was learned, including what did not go to plan:

- **Five of the ten gallery repos cost nothing to build,** because they are the benchmark's repositories at the benchmark's commits and their snapshots were already in the database. That is also the reason they are in the gallery: a visitor can open the code every published number is about.
- **A pinned gallery map shows the freshness banner.** It says the map is some commits behind the default branch, which is true and intended for a pinned snapshot. It was left as it is.
- **A repository can move.** `encode/starlette` is now `Kludex/starlette`; GitHub redirects, the build stores the new name, and the list was corrected. A gallery row is keyed by the name in the list, and the card shows the name GitHub reports.
- **The demo needed a better target than the first one tried.** Searching `Context.invoke` in click finds an `@overload` stub with no callers, and "What breaks?" is empty for it. The recording uses `set_environ` in requests, the target of benchmark question `requests-impact-003`, where the engine finds 37 affected symbols.
- **The README's GIF is 2.8 MB,** above the repository's 1 MB limit for added files; the limit has an exception for that one file.

What the gate does not show, and nothing here claims:

- **A public deployment** (LCH-1): there is no hosted instance and no public URL. Spend caps and per-client caps are tested in code and on the self-host stack (M12), not in production.
- **The final evaluation** (LCH-3): the test split was not used. Every published number is from 2026-11-12 and 2026-11-13, for one-shot answers on the prompt of that day. Agent mode, multi-agent mode, and the hardened prompt have no accuracy or cost numbers.
- **The demo with a cited answer** (LCH-5): the recording shows no answer, because an answer needs the live model.
- **A published post** (LCH-6 is the draft), and **notes to maintainers** (LCH-7).
- **Post-launch monitoring:** there is nothing to monitor yet.

Found on the way:

- **No setting switches an answering mode off.** A public deployment offers all three modes, so the injection gates of all three are launch requirements (live pass, section A). A mode switch is in the backlog; with it, a launch could start with one-shot answers and cost less.
- `scripts/selfhost_check.py` needs an access token, so it cannot check a deployment after its gate is removed (backlog).
- The gallery list is not inside the images; precomputing on a self-hosted stack mounts it into a one-off container, a command that was written from Compose's documentation and not run (backlog).
