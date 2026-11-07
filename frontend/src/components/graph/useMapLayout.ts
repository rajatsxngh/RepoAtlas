"use client";

/**
 * Keeps the map laid out. ELK (in the worker) runs only for containers whose children or edges
 * changed; every other change of size is settled incrementally by moving siblings. Layouts are
 * kept after a container closes, so opening it again with the same children is instant.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ContainerInput, ContainerLayout } from "@/workers/layoutEngine";
import { resizeChild, type Placement } from "./incremental";
import type { LayoutClient } from "./layoutClient";
import type { PlannedChild, PlannedContainer } from "./layoutPlan";
import { containerSize, type Size } from "./sizes";

export type BaseLayout = ContainerLayout & { signature: string };

/** Debounce for layout requests, so quick successive changes make one request. */
export const LAYOUT_DEBOUNCE_MS = 24;

function sizeOf(child: PlannedChild, placed: ReadonlyMap<string, Placement>): Size {
  if (!child.nested) return child.leaf;
  const inner = placed.get(child.id);
  return inner ? containerSize(inner.content, child.label) : child.leaf;
}

/** Placements after settling every child's current size into its container, children first. */
export function settle(
  structure: readonly PlannedContainer[],
  base: ReadonlyMap<string, BaseLayout>,
): Map<string, Placement> {
  const placed = new Map<string, Placement>();
  for (const container of structure) {
    const layout = base.get(container.id);
    if (!layout) continue;
    let placement: Placement = {
      positions: layout.positions,
      sizes: layout.sizes,
      content: layout.content,
    };
    for (const child of container.children) {
      const recorded = placement.sizes[child.id];
      if (!recorded || !placement.positions[child.id]) continue;
      const current = sizeOf(child, placed);
      if (recorded.width !== current.width || recorded.height !== current.height) {
        placement = resizeChild(placement, child.id, current);
      }
    }
    placed.set(container.id, placement);
  }
  return placed;
}

/** The worker request for the containers that need ELK, children before parents. */
export function layoutRequest(
  dirty: readonly PlannedContainer[],
  placed: ReadonlyMap<string, Placement>,
): ContainerInput[] {
  const dirtyIds = new Set(dirty.map((container) => container.id));
  return dirty.map((container) => ({
    id: container.id,
    label: container.label,
    edges: container.edges,
    children: container.children.map((child) => {
      const nested = child.nested && dirtyIds.has(child.id);
      const size = nested ? child.leaf : sizeOf(child, placed);
      return { id: child.id, width: size.width, height: size.height, nested };
    }),
  }));
}

export interface MapLayout {
  placed: ReadonlyMap<string, Placement>;
  /** Containers waiting for ELK. */
  pending: ReadonlySet<string>;
  /** Milliseconds the worker took for the last applied request. */
  lastMs: number | null;
  /** Forgets every layout, so ELK lays out the whole visible map again. */
  relayout: () => void;
}

export function useMapLayout(
  structure: readonly PlannedContainer[],
  client: LayoutClient | null,
): MapLayout {
  const [base, setBase] = useState<ReadonlyMap<string, BaseLayout>>(() => new Map());
  const [lastMs, setLastMs] = useState<number | null>(null);
  const placed = useMemo(() => settle(structure, base), [structure, base]);
  const dirty = useMemo(
    () =>
      structure.filter((container) => base.get(container.id)?.signature !== container.signature),
    [structure, base],
  );
  const pending = useMemo(() => new Set(dirty.map((container) => container.id)), [dirty]);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!dirty.length || !client) return;
    const signatures = new Map(dirty.map((container) => [container.id, container.signature]));
    const timer = setTimeout(() => {
      client
        .request(layoutRequest(dirty, placed))
        .then((result) => {
          if (!result || !alive.current) return;
          setLastMs(result.ms);
          setBase((previous) => {
            const next = new Map(previous);
            for (const layout of result.layouts) {
              const signature = signatures.get(layout.id);
              if (signature !== undefined) next.set(layout.id, { ...layout, signature });
            }
            return next;
          });
        })
        .catch((error: unknown) => {
          if (alive.current) console.error("Layout failed", error);
        });
    }, LAYOUT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [dirty, placed, client]);

  const relayout = useCallback(() => setBase(new Map()), []);
  return { placed, pending, lastMs, relayout };
}
