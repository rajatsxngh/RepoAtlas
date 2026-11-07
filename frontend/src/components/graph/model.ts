/**
 * What the map shows: the children of expanded containers, capped, with the edges between visible
 * nodes. Pure data, no React, so the rules are easy to test.
 *
 * Edge sources:
 * - Directory and file level: the root's file-level aggregate (`subgraph?parent=dir:.&level=file`)
 *   lists every file-to-file edge once. Each is lifted to the nearest visible stand-in of its two
 *   files, so expanding a folder replaces its aggregated edges by edges to its children. Until that
 *   aggregate arrives, the root's own subgraph edges cover the top level.
 * - Inside a file or symbol: that container's own subgraph edges (between its children).
 *
 * Every edge ends up on the deepest visible node the loaded data can place it on.
 */

import type { AggregatedEdge, ApiNode, EdgeKind, Subgraph, SubgraphLevel } from "@/lib/api/types";
import { ROOT_KEY, derivedAncestors, fileKeyOf, parseKey } from "@/lib/graph/keys";

export const ROOT = ROOT_KEY;
const MORE_PREFIX = "more:";
/** Room worth making (by closing other containers) when opening one at the cap. */
const USEFUL_ROOM = 24;

export function moreId(container: string): string {
  return `${MORE_PREFIX}${container}`;
}

export function isMoreId(id: string): boolean {
  return id.startsWith(MORE_PREFIX);
}

export function containerOfMore(id: string): string {
  return id.slice(MORE_PREFIX.length);
}

export type VisibleNode =
  | {
      type: "node";
      id: string;
      node: ApiNode;
      /** The expanded container this node sits in; null at the top level. */
      parent: string | null;
      expanded: boolean;
      depth: number;
    }
  | {
      type: "more";
      id: string;
      container: string;
      parent: string | null;
      /** Children of the container that are not shown individually. */
      hidden: string[];
      depth: number;
    };

export interface VisibleEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  count: number;
  minConfidence: number;
  maxConfidence: number;
}

interface Loaded {
  level: SubgraphLevel;
  children: string[];
  edges: AggregatedEdge[];
}

/** The level whose subgraph lists a node's children on the map. */
export function childLevel(node: Pick<ApiNode, "kind">): SubgraphLevel {
  return node.kind === "dir" ? "dir" : "symbol";
}

/** Directories and parsed files always have children; classes usually do; others rarely. */
export function mayHaveChildren(node: ApiNode): boolean {
  if (node.kind === "dir") return true;
  if (node.kind === "file") return !node.meta.skip_reason;
  return node.kind === "class";
}

function edgeId(source: string, target: string, kind: string): string {
  return `${source}|${target}|${kind}`;
}

export class GraphModel {
  /** Every node the map has seen, by key. */
  readonly nodes = new Map<string, ApiNode>();
  readonly expanded = new Set<string>([ROOT]);
  protected readonly loaded = new Map<string, Loaded>();
  protected readonly shown = new Map<string, Set<string>>();
  protected fileEdges: AggregatedEdge[] | null = null;
  private sizeMemo = new Map<string, number>();
  /** Bumped on every change; consumers compare it to know when to recompute. */
  version = 0;

  constructor(public cap: number) {}

  protected touch(): void {
    this.version += 1;
  }

  addNodes(nodes: readonly ApiNode[]): void {
    let changed = false;
    for (const node of nodes) {
      if (!this.nodes.has(node.key)) {
        this.nodes.set(node.key, node);
        changed = true;
      }
    }
    if (changed) {
      this.sizeMemo = new Map();
      this.touch();
    }
  }

  /** Registers a container's children and the edges between them. */
  addSubgraph(subgraph: Subgraph): void {
    this.addNodes([subgraph.parent, ...subgraph.nodes]);
    this.loaded.set(subgraph.parent.key, {
      level: subgraph.level,
      children: subgraph.nodes.map((node) => node.key),
      edges: subgraph.edges,
    });
    if (subgraph.parent.key === ROOT && !this.shown.has(ROOT)) {
      this.shown.set(ROOT, new Set(this.pickShown(ROOT, this.cap)));
    }
    this.touch();
  }

  /** Registers the root's file-level aggregate: every file and every file-to-file edge. */
  addFileAggregate(subgraph: Subgraph): void {
    this.addNodes(subgraph.nodes);
    this.fileEdges = subgraph.edges;
    this.sizeMemo = new Map();
    this.touch();
  }

  get hasFileEdges(): boolean {
    return this.fileEdges !== null;
  }

  isLoaded(key: string): boolean {
    return this.loaded.has(key);
  }

  childrenOf(key: string): readonly string[] | undefined {
    return this.loaded.get(key)?.children;
  }

  /** Relative size, to pick the largest children when not all fit: lines of code. */
  sizeOf(key: string): number {
    const memo = this.sizeMemo.get(key);
    if (memo !== undefined) return memo;
    const node = this.nodes.get(key);
    let size = 1;
    if (node?.kind === "file") {
      size = typeof node.meta.loc === "number" ? Math.max(1, node.meta.loc) : 1;
    } else if (node?.kind === "dir") {
      const prefix = node.path === "." ? "" : `${node.path}/`;
      size = 0;
      for (const other of this.nodes.values()) {
        if (other.kind === "file" && other.path.startsWith(prefix)) {
          size += typeof other.meta.loc === "number" ? Math.max(1, other.meta.loc) : 1;
        }
      }
      size = Math.max(1, size);
    } else if (node?.span) {
      size = Math.max(1, node.span.end_line - node.span.start_line + 1);
    }
    this.sizeMemo.set(key, size);
    return size;
  }

  /** At most `room` children: all if they fit, else the largest `room - 1` plus a "more" node. */
  protected pickShown(container: string, room: number, keep: readonly string[] = []): string[] {
    const children = this.childrenOf(container) ?? [];
    if (children.length <= room) return [...children];
    const slots = Math.max(0, room - 1);
    const kept = children.filter((child) => keep.includes(child));
    const rest = children
      .filter((child) => !keep.includes(child))
      .map((child, index) => ({ child, index, size: this.sizeOf(child) }))
      .sort((a, b) => b.size - a.size || a.index - b.index)
      .slice(0, Math.max(0, slots - kept.length))
      .map((item) => item.child);
    const chosen = new Set([...kept, ...rest]);
    return children.filter((child) => chosen.has(child));
  }

  /** Known ancestors of a node, nearest first, from loaded parents, else from its key. */
  ancestorsOf(key: string): string[] {
    const chain: string[] = [];
    const seen = new Set<string>([key]);
    let current = this.nodes.get(key)?.parent_key ?? null;
    while (current && !seen.has(current)) {
      chain.push(current);
      seen.add(current);
      current = this.nodes.get(current)?.parent_key ?? null;
    }
    if (chain.at(-1) === ROOT) return chain;
    for (const derived of derivedAncestors(chain.at(-1) ?? key)) {
      if (!seen.has(derived)) {
        chain.push(derived);
        seen.add(derived);
      }
    }
    return chain;
  }

  /** The child of `container` that is `target` or holds it, if the loaded data can tell. */
  childContaining(
    container: string,
    target: string,
    ancestors: ReadonlySet<string> = new Set(this.ancestorsOf(target)),
  ): string | null {
    const children = this.childrenOf(container);
    if (!children?.length) return null;
    if (children.includes(target)) return target;
    for (const child of children) if (ancestors.has(child)) return child;
    const parsed = parseKey(target);
    const targetNode = this.nodes.get(target);
    const path = targetNode?.path ?? (parsed && "path" in parsed ? parsed.path : null);
    if (path === null) return null;
    const holder = this.nodes.get(container);
    if (holder?.kind === "dir") {
      for (const child of children) {
        const node = this.nodes.get(child);
        if (!node) continue;
        if (node.kind === "dir" && path.startsWith(`${node.path}/`)) return child;
        if (node.kind === "file" && node.path === path) return child;
      }
      return null;
    }
    const qualname =
      targetNode?.qualified_name ?? (parsed?.type === "symbol" ? parsed.qualname : null);
    if (!qualname) return null;
    for (const child of children) {
      const node = this.nodes.get(child);
      if (!node || node.path !== path) continue;
      const name = node.qualified_name;
      if (qualname.startsWith(`${name}.`) || qualname.startsWith(`${name} > `)) return child;
    }
    return null;
  }

  isShownIn(container: string, child: string): boolean {
    return this.shown.get(container)?.has(child) ?? false;
  }

  /**
   * The visible node that stands for `key`: itself, its nearest collapsed ancestor, or the
   * "more" node of the container hiding it. Null when the loaded data can't place it.
   */
  proxyOf(key: string): string | null {
    if (key === ROOT) return null;
    const ancestors = new Set(this.ancestorsOf(key));
    let container = ROOT;
    for (let guard = 0; guard < 64; guard += 1) {
      const child = this.childContaining(container, key, ancestors);
      if (child === null) return container === ROOT ? null : container;
      if (!this.isShownIn(container, child)) return moreId(container);
      if (child === key || !this.expanded.has(child)) return child;
      container = child;
    }
    return null;
  }

  isVisible(key: string): boolean {
    return this.proxyOf(key) === key;
  }

  /** Visible nodes, parents before their children (React Flow's order). */
  visibleNodes(): VisibleNode[] {
    const out: VisibleNode[] = [];
    const visit = (container: string, parent: string | null, depth: number) => {
      const children = this.childrenOf(container) ?? [];
      const shown = this.shown.get(container) ?? new Set<string>();
      const hidden: string[] = [];
      for (const child of children) {
        if (!shown.has(child)) {
          hidden.push(child);
          continue;
        }
        const node = this.nodes.get(child);
        if (!node) continue;
        const expanded = this.expanded.has(child) && this.isLoaded(child);
        out.push({ type: "node", id: child, node, parent, expanded, depth });
        if (expanded) visit(child, child, depth + 1);
      }
      if (hidden.length) {
        out.push({ type: "more", id: moreId(container), container, parent, hidden, depth });
      }
    };
    if (this.isLoaded(ROOT)) visit(ROOT, null, 0);
    return out;
  }

  visibleCount(): number {
    return this.visibleNodes().length;
  }

  /** Edges between visible nodes, each lifted to the deepest visible stand-ins and merged. */
  visibleEdges(): VisibleEdge[] {
    const merged = new Map<string, VisibleEdge>();
    const proxies = new Map<string, string | null>();
    const proxy = (key: string) => {
      if (!proxies.has(key)) proxies.set(key, this.proxyOf(key));
      return proxies.get(key)!;
    };
    const add = (edge: AggregatedEdge) => {
      const source = proxy(edge.src);
      const target = proxy(edge.dst);
      if (!source || !target || source === target) return;
      const id = edgeId(source, target, edge.kind);
      const existing = merged.get(id);
      if (existing) {
        existing.count += edge.count;
        existing.minConfidence = Math.min(existing.minConfidence, edge.min_confidence);
        existing.maxConfidence = Math.max(existing.maxConfidence, edge.max_confidence);
      } else {
        merged.set(id, {
          id,
          source,
          target,
          kind: edge.kind,
          count: edge.count,
          minConfidence: edge.min_confidence,
          maxConfidence: edge.max_confidence,
        });
      }
    };
    if (this.fileEdges) {
      for (const edge of this.fileEdges) add(edge);
    }
    for (const [container, loaded] of this.loaded) {
      if (!this.expanded.has(container)) continue;
      const directoryLevel = loaded.level !== "symbol";
      if (directoryLevel && this.fileEdges) continue;
      if (container !== ROOT && !this.isVisible(container)) continue;
      for (const edge of loaded.edges) add(edge);
    }
    return [...merged.values()];
  }

  /** The file a node belongs to, for code reads. */
  fileOf(key: string): string | null {
    return fileKeyOf(key);
  }

  // --- expanding and collapsing -------------------------------------------------------------

  private expansionOrder = new Map<string, number>();
  private expansions = 0;

  /** Expanded containers below `key` (not `key` itself). */
  private expandedBelow(key: string): string[] {
    return [...this.expanded].filter(
      (other) => other !== ROOT && other !== key && this.ancestorsOf(other).includes(key),
    );
  }

  /**
   * Closes the least recently opened containers that are not in `protect` until `room` more
   * nodes fit under the cap. Returns the containers it closed.
   */
  makeRoom(room: number, protect: ReadonlySet<string>): string[] {
    const closed: string[] = [];
    const candidates = [...this.expanded]
      .filter((key) => key !== ROOT && !protect.has(key))
      .sort((a, b) => (this.expansionOrder.get(a) ?? 0) - (this.expansionOrder.get(b) ?? 0));
    for (const key of candidates) {
      if (this.cap - this.visibleCount() >= room) break;
      if (!this.expanded.has(key)) continue;
      // Closing an outer container also closes everything inside it; skip ones that hold a
      // protected container.
      if (this.expandedBelow(key).some((inner) => protect.has(inner))) continue;
      this.collapse(key);
      closed.push(key);
    }
    return closed;
  }

  /**
   * Hides the smallest shown children that are not in `protect` (into their containers' "more"
   * nodes) until `room` more nodes fit under the cap. For revealing a node when closing other
   * containers is not enough. Returns the hidden children.
   */
  hideOthers(room: number, protect: ReadonlySet<string>): string[] {
    const candidates: { container: string; child: string; size: number }[] = [];
    for (const container of this.expanded) {
      for (const child of this.shown.get(container) ?? []) {
        if (protect.has(child) || this.expanded.has(child)) continue;
        candidates.push({ container, child, size: this.sizeOf(child) });
      }
    }
    candidates.sort((a, b) => a.size - b.size);
    const hidden: string[] = [];
    for (const { container, child } of candidates) {
      if (this.cap - this.visibleCount() >= room) break;
      this.shown.get(container)?.delete(child);
      hidden.push(child);
    }
    if (hidden.length) this.touch();
    return hidden;
  }

  /**
   * Expands a loaded, visible container. Shows all its children if they fit under the cap, else
   * the largest ones and a "more" node. With `makeRoom`, closes the least recently opened other
   * containers first when the cap leaves too little room.
   */
  expand(
    key: string,
    options: {
      keep?: readonly string[];
      makeRoom?: boolean;
      hideOthers?: boolean;
      /** Never closed or hidden to make room (for example other nodes being revealed). */
      protect?: ReadonlySet<string>;
    } = {},
  ): ExpandOutcome {
    if (key === ROOT) return { ok: true, closed: [] };
    const children = this.childrenOf(key);
    if (!children) return { ok: false, reason: "not-loaded", closed: [] };
    if (!children.length) return { ok: false, reason: "empty", closed: [] };
    if (!this.isVisible(key)) return { ok: false, reason: "hidden", closed: [] };
    if (this.expanded.has(key)) return { ok: true, closed: [] };
    // Opening needs room for every child, or at least for the kept ones plus a "more" node.
    const minimum = Math.min(children.length, Math.max(2, (options.keep?.length ?? 0) + 1));
    // When closing others to make room, aim for enough room to be useful.
    const useful = Math.min(children.length, Math.max(minimum, USEFUL_ROOM));
    let closed: string[] = [];
    const protect = new Set([
      key,
      ...this.ancestorsOf(key),
      ...(options.keep ?? []),
      ...(options.protect ?? []),
    ]);
    if (options.makeRoom && this.cap - this.visibleCount() < useful) {
      closed = this.makeRoom(useful, protect);
    }
    if (options.hideOthers && this.cap - this.visibleCount() < minimum) {
      this.hideOthers(minimum, protect);
    }
    const room = this.cap - this.visibleCount();
    if (room < minimum) return { ok: false, reason: "cap", closed };
    this.shown.set(key, new Set(this.pickShown(key, room, options.keep)));
    this.expanded.add(key);
    this.expansions += 1;
    this.expansionOrder.set(key, this.expansions);
    this.touch();
    return { ok: true, closed };
  }

  /** Collapses a container and everything expanded inside it. */
  collapse(key: string): void {
    if (key === ROOT || !this.expanded.has(key)) return;
    for (const inner of [key, ...this.expandedBelow(key)]) {
      this.expanded.delete(inner);
      this.shown.delete(inner);
      this.expansionOrder.delete(inner);
    }
    this.touch();
  }

  /** Collapses every container. */
  collapseAll(): void {
    for (const key of [...this.expanded]) if (key !== ROOT) this.collapse(key);
  }

  /** Hidden children of an expanded container (the members of its "more" node). */
  hiddenIn(container: string): string[] {
    const shown = this.shown.get(container);
    return (this.childrenOf(container) ?? []).filter((child) => !shown?.has(child));
  }

  /**
   * Shows more of a container's hidden children, largest first, as far as the cap allows.
   * Returns how many were revealed (0 when the cap is reached).
   */
  showMore(container: string): number {
    const hidden = this.hiddenIn(container);
    const shown = this.shown.get(container);
    if (!hidden.length || !shown) return 0;
    const count = this.visibleCount();
    // Revealing every hidden child also removes the "more" node, which frees one slot.
    const revealAll = hidden.length <= this.cap - count + 1;
    const take = revealAll ? hidden.length : this.cap - count;
    if (take <= 0) return 0;
    const picked = hidden
      .map((child, index) => ({ child, index, size: this.sizeOf(child) }))
      .sort((a, b) => b.size - a.size || a.index - b.index)
      .slice(0, take)
      .map((item) => item.child);
    for (const child of picked) shown.add(child);
    this.touch();
    return picked.length;
  }

  /** Makes sure `child` is shown in `container` (used when revealing a node). */
  showChild(container: string, child: string, keep: ReadonlySet<string> = new Set()): boolean {
    const shown = this.shown.get(container);
    if (!shown) return false;
    if (shown.has(child)) return true;
    // Showing the last hidden child removes the "more" node, so it costs nothing.
    const costs = this.hiddenIn(container).length > 1;
    if (costs && this.visibleCount() >= this.cap) {
      const protect = new Set([child, container, ...this.ancestorsOf(container), ...keep]);
      this.makeRoom(1, protect);
      if (this.visibleCount() >= this.cap) this.hideOthers(1, protect);
      if (this.visibleCount() >= this.cap) return false;
    }
    shown.add(child);
    this.touch();
    return true;
  }
}

export type ExpandOutcome =
  | { ok: true; closed: string[] }
  | { ok: false; reason: "not-loaded" | "empty" | "hidden" | "cap"; closed: string[] };
