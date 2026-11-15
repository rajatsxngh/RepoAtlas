# 0025 — M17 without spend: the launch's build gate, and what moved

**Status:** Accepted · **Date:** 2026-11-16

## Context

M17 is the launch: a public deployment, ten precomputed repos, a final evaluation, the README,
a demo video, and the launch post. Decision 0020 says the build spends nothing: no Anthropic
API calls, no hosting, and nothing published without the owner. Three of M17's eight tasks
need exactly those (the final evaluation, going public, a week of watching production), and
two more are planned around a public URL (the demo recording and the gallery that visitors
open).

This record applies the replan M12 to M16 used. Everything a launch needs that costs nothing
is built and checked now; everything else is listed as pending and stays the owner's decision.

## Decision

1. **The product stays as designed.** Nothing is removed or made cheaper to fit the
   constraint.
2. **The gallery is built in full.** Building a map calls no model: the stages are fetch,
   inventory, parse, graph, store, chunk, embed (a local embedding model, decision 0009), and
   publish, and none of them imports the LLM client. So the list, the precompute command, the
   endpoint, and the landing-page gallery are built, tested, and run on the local stack with
   the ten real repos. What is pending is running the same command on the hosted database.
3. **Gallery membership lives in the database,** in a new table `gallery` (migration `0010`),
   written by `repoatlas precompute` and read by `GET /gallery`. The list file
   (`launch/repos.yaml`) is an input of the command, not of the running API, so the images
   need no extra file and a deployment shows exactly what was precomputed on its database. An
   entry appears only while its snapshot is ready. Titles and descriptions in the list are
   written by us; nothing in a gallery card comes from repository content.
4. **The ten repos** are permissively licensed (MIT, BSD-3-Clause, Apache-2.0), far below the
   size caps, six Python and four TypeScript, and include all five benchmark repos at the
   benchmark's commits, so every published number is about code a visitor can open.
5. **The results page is written from the reports that exist,** and says so. `docs/results.md`
   publishes the M10 baselines and the M11 impact results (measured on 2026-11-12 and
   2026-11-13, live, on the frozen test split) with their intervals, costs, losses, and known
   biases. It has a table of what is not measured: agent and multi-agent modes, the one-shot
   mode on the hardened prompt, cost after caching. A test compares the page's numbers with
   the report files. **The frozen test split is not touched in this milestone**; the final
   run "of every system at its final settings" is a live-pass item.
6. **The demo is recorded on the local stack and shows only what no model wrote:** building a
   map, exploring it, search, a path, and impact analysis (graph only, $0). No answer text is
   shown, so no stand-in answer can be mistaken for a model's. The script
   (`docs/launch/demo-script.md`) is the planned 45 seconds including the cited answer; the
   recording made now covers the scenes that need no model and is labelled that way. The full
   recording on the public URL is a live-pass item.
7. **The launch post is a draft with every number sourced** to a report file, and with the
   placeholders the owner fills at launch (the URL, the final numbers if the final run changes
   them). The owner publishes it.
8. **A link check replaces "README links all resolve" for now:**
   `python scripts/check_links.py` checks every relative link and heading anchor of the README
   and the docs it names, in CI. `--external` also requests the web links; it is run by hand
   (network), and its result is recorded at the gate.
9. **Going public is a checklist, not an action:** `docs/launch/go-public.md` lists the steps
   in order (which live-pass items, which variables, what to verify, how to roll back). The
   M17 gate is a **build gate**; M17 closes as Done for the build with its live items pending
   in [`docs/live-pass.md`](../live-pass.md).
10. **The live pass gets an order.** The list has grown to cover seven milestones; its first
    section now says which items a launch needs, in which order, with their caps, and which
    are optional. One gap is recorded rather than built: there is no setting to switch an
    answering mode off in a deployment, so a public deployment exposes agent and multi-agent
    mode, and their injection gates (items 12 and 16) are therefore launch requirements. A
    mode switch is in the backlog; with it, a launch could start with one-shot answers only.

### What changes in M17

| Planned | At the M17 gate | Live pass |
|---|---|---|
| T1 gallery repos | As planned | — |
| T2 precompute and gallery | As planned, on the local stack; Playwright in mock mode and against the real API | `repoatlas precompute` on the hosted database |
| T3 final evaluation, `docs/results.md` | `docs/results.md` from the existing M10 and M11 reports, checked against them; pending table | Every system on the test split, three runs each; the page updated |
| T4 README | As planned; numbers labelled measured or pending; the demo GIF is the local recording | Swap in the full recording |
| T5 demo video | Script; a local recording of the scenes that need no model | The 45-second recording on the public URL, with a live cited answer |
| T6 launch post drafts | As planned (drafts) | The owner publishes |
| T7 go public | Link check in CI; go-public checklist; the live-pass order | Remove the gate; verify caps and alerts in production; smoke test |
| T8 post-launch checks | Results filled in; backlog triage | A week of spend and error monitoring |

LCH-1 (public deployment), LCH-3 (final evaluation), and the recording half of LCH-5 are
therefore **built for but not delivered** at the M17 gate. LCH-7 (notes to maintainers, P2)
is the owner's choice after launch.

### Contract additions in earlier milestones

All are backward compatible; the milestone files list them under "Added later by decision
0025".

- **Store (M03).** Table `gallery` (migration `0010_gallery`).
- **API (M04).** `GET /gallery`; `repoatlas precompute`.
- **Web app (M05).** `getGallery`, the gallery on the landing page, and gallery entries in
  mock mode.

## Consequences

- At the end of the build nobody outside the owner's machine can use RepoAtlas. The launch is
  the live pass.
- The published numbers are real but dated: they were measured before the prompt was hardened
  (M13) and before agent and multi-agent modes existed. The results page and the README say
  so wherever a number appears.
- The demo GIF in the README shows the map and impact analysis, not a cited answer, until the
  live recording replaces it.
- The gallery on a deployment is whatever was precomputed there; an empty table shows no
  gallery, and the landing page works without it.
