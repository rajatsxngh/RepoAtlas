# 0008 — Map with React Flow, elkjs, and highlight layers

**Status:** Accepted · **Date:** 2026-11-07

## Context

Code maps fail in predictable ways: thousands of nodes on screen, nodes laid out in one long row, tangled edges, and frozen UIs on large repos. Several features (citations, impact results, paths) need to draw on the same map.

## Decision

- Next.js with React Flow for rendering and elkjs (layered layout) in a web worker.
- The map opens at the top directory level; folders are containers; edges between containers are aggregated with counts; expansion is lazy; a node cap groups the rest under "+N more"; layout is recomputed per expanded container so the map doesn't jump.
- Overlays use generic highlight layers `{id, nodeKeys, edgeKeys, style, scores, label}` that compose and bubble up to visible ancestors.
- Repo text is always rendered as text, never HTML.

## Consequences

- Readable maps for large repos without client freezes.
- New features add layers and node actions instead of changing the canvas.
- Idea credit: Understand-Anything's dashboard work on ELK layout, containers, and edge aggregation.
