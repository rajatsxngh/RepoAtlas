# M05 — Interactive map

**Type of work:** Frontend · **Size:** L (11 tasks) · **Status:** In progress

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
- [ ] **T9 — Search and reveal.** `SearchBox`, `revealNode`, `focusNode`.
- [ ] **T10 — Highlight layers and path finder.** Layer composition and bubbling to visible ancestors (unit tests on the bubbling logic); path finder built on layers.
- [ ] **T11 — End-to-end and stress tests.** Playwright against the real API with the fixture repos; stress test with the synthetic graph; XSS fixture (script and HTML in comments, docstrings, file names) renders as inert text.

## Exit criteria

- [ ] Playwright passes end to end on a fixture repo: submit URL → progress → map → expand → node panel with code → path highlighted.
- [ ] Synthetic 10,000-node graph: never more than the node cap visible, and each expansion's layout finishes in under 2 seconds.
- [ ] The XSS fixture renders as plain text everywhere (map labels, panel, code viewer).
- [ ] Layer bubbling unit tests pass (hidden descendants show on the nearest visible ancestor with max score and count).
- [ ] Manual review on 3 real repos (small, medium, large) with notes in Results.

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

_Filled in at the gate._
