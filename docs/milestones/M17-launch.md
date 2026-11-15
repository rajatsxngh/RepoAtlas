# M17 — Launch

**Type of work:** Release and content · **Size:** M (8 tasks) · **Status:** In progress

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
- [ ] **T8 — Gate.** The ten repos precomputed on the local stack; gate evidence; backlog triage; Results filled in. Post-launch monitoring is a live-pass item.

## Exit criteria

- [ ] All 10 gallery repos are precomputed on the local stack and open from the landing page without a build (the snapshot is ready before the click).
- [ ] `docs/results.md` publishes the measured test-split results with intervals and cost, names what is pending, and matches the report files (checked by a test).
- [ ] The README's links resolve (`python scripts/check_links.py`, and once with `--external`); the demo script is written and the local recording is in the README; the launch post is drafted.
- [ ] The go-public checklist and the live-pass order exist, and every launch-time live item is listed as pending.

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

_Filled in at the gate._
