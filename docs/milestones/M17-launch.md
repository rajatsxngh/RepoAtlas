# M17 — Launch

**Type of work:** Release and content · **Size:** M (8 tasks) · **Status:** Not started

## Goal

Make RepoAtlas public and tell the story once — with a working demo, precomputed repos that load instantly, and published numbers.

## Depends on

- **M00–M16 (all earlier milestones)** — in particular: the private deployment, limits, and runbook (M12); the guardrail gate results (M13); all answering modes and their reports (M10, M11, M14, M15); cost and incremental builds (M16).

## Provides

- **Public deployment:** the access gate removed; spend caps and per-client caps verified in production; monitoring of daily spend and error rates.
- **Precomputed gallery:** `launch/repos.yaml` (10 well-known repos at pinned commits) and `repoatlas precompute --list launch/repos.yaml`; a gallery on the landing page linking straight to ready snapshots.
- **Final results:** every system on the frozen test split (3 runs each), published as `docs/results.md` with tables, charts, confidence intervals, cost per question, and a "where RepoAtlas loses" section.
- **README:** demo GIF at the top, one-line pitch, architecture diagram, results summary, cost per question, self-hosting guide link, related projects and credits, license.
- **Demo video:** a 45-second script in `docs/launch/demo-script.md` and the recording (paste URL → map animates → "What breaks if I change X?" → cited answer with the path highlighted).
- **Launch post:** `docs/launch/linkedin-post.md` (drafted; the owner publishes it), with optional drafts for Show HN, Reddit, and X.

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

- [ ] **T1 — Choose gallery repos.** Ten repos, pinned commits, `launch/repos.yaml`.
- [ ] **T2 — Precompute and gallery.** `repoatlas precompute`; gallery UI; Playwright test that gallery snapshots open instantly.
- [ ] **T3 — Final evaluation.** All systems on the test split, 3 runs each; `docs/results.md`.
- [ ] **T4 — README.** Demo GIF, architecture diagram, results, cost, self-hosting, credits.
- [ ] **T5 — Demo video.** Script and recording.
- [ ] **T6 — Launch post drafts.** LinkedIn post (and optional others) for the owner to publish.
- [ ] **T7 — Go public.** Remove the access gate; verify caps and alerts in production; smoke test.
- [ ] **T8 — Post-launch checks.** One week of spend and error monitoring; backlog triage; Results filled in.

## Exit criteria

- [ ] The public URL is healthy, with spend caps and per-client caps verified in production.
- [ ] All 10 gallery repos open instantly from the landing page.
- [ ] `docs/results.md` publishes every system's test-split results with intervals and cost.
- [ ] README links all resolve; the demo video is recorded; the launch post is drafted.

## Verification

```bash
cd backend
uv run repoatlas precompute --list ../launch/repos.yaml
uv run repoatlas eval run --system <each> --split test --runs 3
cd ../frontend && npx playwright test gallery
```

Manual: open the public URL in a private browser window and run the demo script end to end.

## Risks and open questions

- A traffic spike after the post; precomputed repos, caps, and the spend guard protect costs.
- Final numbers may be less impressive than hoped; the post leads with what is true.
- Open: which gallery repos best show off "What breaks?".

## Results

_Filled in at the gate._
