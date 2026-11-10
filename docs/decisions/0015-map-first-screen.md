# 0015 — A map that explains itself: overview, edge links, readable layout

**Status:** Accepted · **Date:** 2026-11-10 · **Amends:** 0007 (API surface), 0008 (map)

## Context

After M05 the map's first screen showed the top folders with unlabeled, tangled edges. It said nothing about where to start, how strongly folders depend on each other, or why, and its top-to-bottom order didn't follow dependencies (test links and guessed calls pulled boxes the wrong way). Reviewing it, the owner asked: "how will this help us understand anything at all?"

## Decision

- **Overview.** `GET /snapshots/{id}/overview` returns public symbols (most used from other files first), routes outside test files, the files and symbols most used from other files (imports and calls; name-matched guesses below 0.6 left out), node counts, and how many source files have linked tests. The workspace opens with an `OverviewPanel` ("Start here") beside the map; every item reveals its node.
- **Edge links.** `GET /snapshots/{id}/links?from=&to=&kind=&limit=` returns the individual edges behind an aggregated edge, strongest first, with sites and both end nodes; `total` equals the aggregated count. Clicking a line opens an `EdgePanel` with that list.
- **Readable layout.** Only "uses" edges shape the layout, from user to used: imports, calls, and inheritance as they point, test links flipped (the test uses the code), route links and pure guesses left out. Widely used code settles at the bottom.
- **Readable edges.** Edges made only of guesses are hidden by default (a legend toggle shows them). Counts show from mid zoom, and at every zoom while at most 40 edges are drawn. Lines into an opened box from outside it are faint until one of their ends is hovered or selected. The minimap appears only above 24 nodes on screen.

Both endpoints read the in-memory adjacency; no tables change.

## Consequences

- A first-time reader gets a starting point and can ask "why is this line here?" of any line.
- The overview is a heuristic (cross-file use counts, public markers); it is not an importance score and is not evaluated.
- Later overlays (answers, impact) land on a map that already reads well.
