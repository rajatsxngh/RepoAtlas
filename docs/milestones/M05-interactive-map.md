# M05 — Interactive map

**Type of work:** Frontend · **Size:** L (11 tasks) · **Status:** Done

## Goal

A fast, readable map of any snapshot that starts zoomed out and expands on demand, built from reusable pieces — a canvas with overlay layers, a code viewer, search — that other features can plug into.

## Depends on

- **M04** — REST endpoints (`POST /repos`, `GET /jobs/{id}`, snapshot, subgraph, node, locate, neighbors, search, path, code), error model, generated TypeScript types in `frontend/src/lib/api/schema.d.ts`, rate-limit behavior (429 + `Retry-After`).

## Provides

- **App:** `frontend/` — Next.js (App Router) + TypeScript + Tailwind + TanStack Query + Zustand; ESLint, Prettier, Vitest, Playwright; CI job `frontend` (lint, typecheck, unit tests, e2e against the API).
- **API client:** `src/lib/api/client.ts` — typed fetch wrapper over the generated types, with error mapping and 429 backoff; mock mode (`NEXT_PUBLIC_API_MOCK=1`) serving fixtures for development and stress tests.
- **Routes:** `/` (landing + URL input + progress) and `/s/[snapshotId]` (map workspace).
- **`GraphCanvas`** (`src/components/graph/GraphCanvas.tsx`)
  - Props: `snapshotId`, `layers: HighlightLayer[]`, `onSelect(nodeKey)`, `onNodeAction(nodeKey, actionId)`, `nodeActions?: NodeAction[]`.
  - Imperative handle (`useRef`): `revealNode(key)`, `focusNode(key)`, `fitView()`, `expand(key)`, `collapse(key)`.
- **`HighlightLayer`** (`src/components/graph/layers.ts`):
  ```ts
  type HighlightLayer = {
    id: string;
    nodeKeys: string[];
    edgeKeys?: string[];          // "src|dst|kind"
    style: "primary" | "secondary" | "warning" | "muted";
    scores?: Record<string, number>;  // 0..1, shown as intensity
    label?: string;               // shown in the legend
  };
  ```
  Layers compose (several at once). A layer entry on a hidden node bubbles up to its nearest visible ancestor, which shows the maximum score and a count badge.
- **`NodeAction`** — `{ id, label, appliesTo: NodeKind[] }`; actions appear in the node panel and context menu and call `onNodeAction`.
- **`CodeViewer`** (`src/components/code/CodeViewer.tsx`) — props `snapshotId`, `path`, `startLine`, `endLine`, `highlightRanges?`; fetches `/code`; syntax highlighting; renders text only.
- **`NodePanel`**, **`SearchBox`**, **`PathFinder`**, **`BuildProgress`**, **`Legend`** components.
- **Layout worker:** `src/workers/layout.worker.ts` (elkjs layered layout) with a typed message protocol.
- **Sanitized markdown:** `src/lib/markdown.tsx` — a renderer with a strict sanitizer (no raw HTML), for any markdown shown in the app.

## Requirements

MAP-1, MAP-2, MAP-3, MAP-4, MAP-5, MAP-6, MAP-7, MAP-8, MAP-9, MAP-10, MAP-11

## Scope

**In:** web app scaffold, build flow with progress, map with containers and lazy expansion, layout worker, styling and legend, node panel, code viewer, search, path finder, highlight layers, node actions, sanitized markdown, tests.

**Out:** asking questions, any LLM features, authentication, deployment.

## Design notes

### Visible graph model
- The map shows the children of expanded containers. It starts with the root directory's children (`/subgraph?parent=dir:.&level=dir`).
- Expanding a directory fetches its children; expanding a file fetches its symbols (`level=symbol`). Results are cached by TanStack Query per (snapshot, parent, level).
- Edges between visible nodes come from the aggregated subgraph responses; when a container is expanded, aggregated edges touching it are replaced by edges to its children.
- **Node cap:** at most 300 visible nodes (configurable). When an expansion would exceed the cap, the largest children are shown and the rest are grouped in a "+N more" node that expands on click.

### Layout
- elkjs `layered` algorithm, direction down, in a web worker; containers are ELK compound nodes.
- Expanding re-lays out only the expanded container's contents within its box, then shifts neighbors; existing node positions are otherwise preserved, so the map doesn't jump.
- Layout requests are debounced; a stale result (older request ID) is discarded.

### Styling
- Node shape and icon by kind (dir, file, class, function, method, route, test). Test nodes use a distinct color.
- Edge color by kind; edges with confidence below 0.6 are dashed; aggregated edges show their count and get thicker with count. The legend explains kinds, confidence, and active layers.
- Highlight styles map to Tailwind tokens; scores set opacity or ring intensity.

### Node panel and code
- Shows kind, symbol key (copyable), path, line range, owners (top 3 with share), caller and callee counts (from `neighbors` with depth 1), registered node actions, and the code viewer for the node's span.
- **Safety:** repo text is always rendered as text nodes; never `dangerouslySetInnerHTML`. The syntax highlighter must produce escaped tokens. File names, symbol names, and owner names are untrusted too.

### Search and path finder
- Search calls `/search` with debounce; selecting a result calls `revealNode` (expands the ancestors in order) and then `focusNode`.
- Path finder: pick two symbols (from search or the panel), call `/path`, and show the result as a `primary` highlight layer, revealing every node on the path.

### Accessibility
- Keyboard focus for the URL input, search, panel controls, and node actions; labels on icon buttons.

### Stress testing
- `frontend/scripts/gen-synthetic-graph.ts` generates a 10,000-node fixture (deep directory tree, dense calls) served by mock mode. A Playwright test expands many containers and measures layout time per expansion.

## Tasks

- [x] **T1 — Scaffold.** Next.js app, Tailwind, ESLint, Prettier, Vitest, Playwright; CI job.
- [x] **T2 — API client and mock mode.** Typed client over generated types; fixtures for mock mode.
- [x] **T3 — Landing and build flow.** URL input with client-side validation hints → `POST /repos` → poll `/jobs/{id}` with stage names → redirect to `/s/[id]`; error states (private, too large, rate limited).
- [x] **T4 — Canvas base.** React Flow with root-level containers and aggregated edges.
- [x] **T5 — Layout worker.** elkjs in a worker, incremental layout within containers, stale-result handling.
- [x] **T6 — Expansion and node cap.** Expand/collapse, "+N more" grouping.
- [x] **T7 — Styling and legend.** Kinds, confidence dashes, counts, layer legend entries.
- [x] **T8 — Node panel and code viewer.** Including owners, counts, node actions, escaped highlighting.
- [x] **T9 — Search and reveal.** `SearchBox`, `revealNode`, `focusNode`.
- [x] **T10 — Highlight layers and path finder.** Layer composition and bubbling to visible ancestors (unit tests on the bubbling logic); path finder built on layers.
- [x] **T11 — End-to-end and stress tests.** Playwright against the real API with the fixture repos; stress test with the synthetic graph; XSS fixture (script and HTML in comments, docstrings, file names) renders as inert text.

## Exit criteria

- [x] Playwright passes end to end on a fixture repo: submit URL → progress → map → expand → node panel with code → path highlighted.
- [x] Synthetic 10,000-node graph: never more than the node cap visible, and each expansion's layout finishes in under 2 seconds.
- [x] The XSS fixture renders as plain text everywhere (map labels, panel, code viewer).
- [x] Layer bubbling unit tests pass (hidden descendants show on the nearest visible ancestor with max score and count).
- [x] Manual review on 3 real repos (small, medium, large) with notes in Results.

## Verification

```bash
cd frontend
npm ci
npm run lint && npm run typecheck && npm test
npm run dev                         # with the API and worker running
npx playwright test
```

## Risks and open questions

- ELK can be slow on very large compound graphs; the node cap and per-container layout bound it.
- React Flow performance with many edges; aggregated edges keep counts low.
- Open: which syntax highlighter gives escaped output with good performance (Shiki vs Prism); decide in T8.

## Results

Gate checked on 2026-11-08 on branch `m05-interactive-map`.

**Tests.** In `frontend/`: `npm run lint` (ESLint with no warnings allowed, then Prettier) and `npm run typecheck` pass, and `npm test` runs 226 Vitest tests in 30 files. `npx playwright test` runs 7 tests in two projects: `app` (3 tests: the production build against the real API and a worker, started by `backend/tests/e2e/stack.py` over local bare git repos behind a fake GitHub transport, in a fresh `<database>_e2e` database) and `mock` (4 tests: a mock-mode build). In `backend/`: `uv run poe check` passes on Windows: 793 passed, 1 skipped (creating symlinks needs extra privileges on Windows), including `tests/integration/api/test_e2e_fixtures.py`, which checks the end-to-end fixture repos build. ESLint bans `dangerouslySetInnerHTML` and the `innerHTML` family outside tests.

**Exit criteria**
- *Playwright passes end to end on a fixture repo: submit URL → progress → map → expand → node panel with code → path highlighted.* `e2e/app/map.spec.ts` submits `repoatlas-e2e/shop`, follows the stages to the map, opens `src`, opens a method's panel and reads its code, and highlights the path from a test to a helper; a second test gets the private-repository error.
- *Synthetic 10,000-node graph: never more than the node cap visible, and each expansion's layout finishes in under 2 seconds.* `e2e/mock/stress.spec.ts` opens the biggest openable node 40 times, then clicks "+N more" pills: median 811 ms, slowest 1,180 ms, and at most 294 visible nodes (cap 300). It times each expansion inside the page and prints the slowest three with their ELK time (25–32 ms here: the rest is fetching, the model, and drawing).
- *The XSS fixture renders as plain text everywhere (map labels, panel, code viewer).* `e2e/app/xss.spec.ts` (through the real API) and `e2e/mock/xss.spec.ts`: payloads in a folder name, file names, a TypeScript test name, comments, docstrings, an owner name, and a route path show as literal text on the map, in the panel, search, the path finder, and the code viewer; no dialog opens and no injected element appears.
- *Layer bubbling unit tests pass.* `src/components/graph/layers.test.ts` (6 tests): a hidden descendant lands on its nearest visible ancestor with the maximum score and a count of hidden entries, layers compose, and style priority is warning, primary, secondary, muted.
- *Manual review on 3 real repos (small, medium, large) with notes in Results.* Below.

**Manual review.** `node scripts/dev.mjs` ran Postgres, the API, a worker, and the web app in dev mode (slower than a production build); repos were built from the landing page and viewed in a browser in light and dark themes.
- Small — `pallets/itsdangerous` (15 files, 165 nodes, 714 edges; built in about 4 s). The top level is three folders and one bundled `tests → src` edge carrying 193 relationships; with the 44 px layer gap tried during the review, its count pill covered the whole edge, so the gap is back at 56 px. Opening `src`, `itsdangerous`, and `tests` took about 270 ms each; the package reads as a mostly vertical chain, which matches its imports. The panel on `signer.py` was right (imported by 6, imports 2, tested by 4 tests; owners David Lord 77%, Armin Ronacher 15%, Hsiaoming Yang 8%), but its connection rows were full paths cut off at the end, so three `tests/test_itsdangerous/…` rows looked alike. Fixed: rows lead with the name, then the folder (files) or file (symbols). Searching `URLSafeTimed` revealed `URLSafeTimedSerializer` inside `url_safe.py` and showed lines 79–83. Fixed: in focus mode, count pills stayed opaque while their edges faded; they fade together now.
- Medium — `pallets/flask` (83 files, 1,915 nodes). The top level is clear, but opening `src/flask` drew a hairball: test links from `tests` to nearly every module drowned the calls and imports. Fixed: the legend toggles edge kinds and test links start hidden (the panel still counts tests); hovering or selecting a node fades every edge not touching it (a CSS rule keyed by node numbers, so no repo text reaches a stylesheet); dense views draw other edges lighter. The layout also spread the package's 20 files over about 2,470 × 1,620 px, unreadable when fitted to the screen. Fixed: ELK lays out each container on every node's 3 strongest outgoing and incoming edges (the map still draws them all), keeps little room for routed edges (the map draws straight ones), and places containers of up to 40 children with network simplex: the same package now takes about 1,290 × 1,190 px. Network simplex for every container came first, but in a benchmark it took 30 s to place a dense 120-node container (1.3 s with Brandes-Koepf), hence the size limit. The panel read well on a core module: imported by 6, imports 12, tested by 15 tests, owners led by David Lord at 73%.
- Large — `fastapi/fastapi` (1,223 files, 9,967 nodes). First paint 834 ms (ELK 124 ms). Opening `docs_src`, `tests`, and `fastapi` took 558 ms, 1,223 ms, and 2,259 ms (measured during the review, before the layout changes described under flask). Opening a third big folder closed `docs_src` with the notice "Closed docs_src to stay under 300 nodes.", and the legend said "1,200 of 1,576 links", so both caps held. Searching `get_openapi` revealed it through two folders and a file and opened the panel (callers 4, callees 9, owner Sebastián Ramírez 89%). Fixed: a second, lighter density tier above 500 drawn edges; long symbol keys and paths broke mid-word in the panel, so they now wrap only after `/`, `:`, and `.`; the favicon was missing.
- Also `colinhacks/zod` (524 files, 5,587 nodes): the top level and `packages → zod → src` read well. Fixed: edges were drawn over container headers (now under them), and the minimap covered nodes in its corner (now smaller).

**Decided: Prism over Shiki (T8).** Prism (`prism-react-renderer`) tokenizes synchronously into token objects that React renders as text nodes, colored by CSS classes, so escaping is React's and no HTML string is ever built. Shiki's main API returns HTML strings and loads grammars and a WASM engine asynchronously. Prism's grammars are coarser than TextMate's, which is fine for reading a span.

**Deviations from the design notes**
- Edges: the map fetches the root's file-level aggregate once (`parent=dir:.&level=file`) and lifts each edge to the nearest visible node, so folder-to-folder edges stay exact for any mix of open and closed containers, instead of swapping aggregated edges per expansion. An open file's symbol edges come from that file's own subgraph; edges from its symbols to other files stay on the file boxes (backlog: symbol edges across open files).
- Edge budget: at most 1,200 edges are drawn (highlighted ones first, then the heaviest), and the legend says "N of M links" when it trims; count labels show only when zoomed in. Without it, the synthetic graph drew about 10,000 edges and expansions took 10–20 s.
- Layout: still ELK layered, top to bottom, per container, but on each node's 3 strongest outgoing and incoming edges (weighted by count), with network-simplex placement up to 40 children and Brandes-Koepf above.
- Additions to Provides, backward compatible: the canvas handle also has `revealNodes(keys)` and `fitView(keys?)` (the path finder reveals and frames a whole path); the legend toggles edge kinds, with `tested_by` off at first; hovering or selecting a node keeps its edges strong and fades the rest.
- The synthetic graph is generated in memory by `src/lib/api/mock/fixtures/synthetic.ts` (seeded, identical every run) when mock mode serves snapshot 4, instead of a `scripts/gen-synthetic-graph.ts` writing a fixture file: no multi-megabyte JSON in the repo.
- Zustand is installed, as the stack lists, but nothing needed a store yet: map state lives in `GraphCanvas`, server state in TanStack Query.
- `BuildProgress` lives in `components/progress/`: the root `.gitignore` ignores `build/`, which hid `components/build` from git and Tailwind.
- XSS payloads follow what git and file systems allow: no `/` in file names, git drops `<`, `>`, and trailing quotes from author names (so the owner payload uses entities and quotes), owners are keyed by email, and TypeScript test cases come only from `*.test.ts` files.
- Not in the design notes: mock mode also simulates build failures by repo name, and a parity test checks its answers against expectations exported from the backend's `GraphStore`; GETs retry a 429 after `Retry-After` (up to 20 s, 3 times) while POSTs never retry; `node scripts/dev.mjs` starts the whole stack.

**Found and fixed at the gate.** After the review fixes, the stress test's slowest expansion rose from about 1.1 s to 1.9 s, for three reasons. Focus mode tagged each edge with a new array per render, which defeated the stabilizer that keeps unchanged React Flow objects, so every edge re-rendered on each update; nodes now get stable numbers. Network simplex ran for every container; it is now limited to 40 children and runs on the strong-edge skeleton. And the test timed expansions by polling the page over the DevTools protocol, which on a busy machine added up to a second each while the page itself took 0.3–0.4 s in a normal browser; it now times them inside the page. Separately, under the whole unit suite in parallel a few tests timed out after testing-library's default 1 s wait; it now waits up to 5 s.

**Seen at the gate, not fixed.** One full backend run failed `test_build_follow_and_read_a_repository_over_http` (an M04 test, untouched since): it expects its first poll to see the job still queued, and while other processes kept this machine busy the worker claimed the job first. It passed three times on its own and in the next full run. The race is in the test, not in the API.

**CI.** Run [42683575919](https://github.com/rajatsxngh/RepoAtlas/actions/runs/42683575919) on `cfb196a` passed all five jobs: the Linux full check (794 passed, with the symlink test and every Postgres suite), the Windows unit tests (581 passed), the API types job, the docs lint, and the new frontend job, which ran lint, typecheck, the 226 unit tests, and all 7 Playwright tests (the real API with a Postgres service, and mock mode) in 1.6 minutes. On the runner the stress test's median expansion took 416 ms and the slowest 515 ms, with at most 294 visible nodes. Both backend jobs printed the same fixture digests as this machine, unchanged since M02: graphs `py_small` `ec01fa4353017159…` and `ts_small` `c066ffa9b52dbd10…`, parses `py_small` `31cf59ec616dfb6a…` and `ts_small` `13b0ed333b8a253e…`.
