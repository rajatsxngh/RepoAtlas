/**
 * An in-memory snapshot graph for mock mode. It answers the read endpoints with the same
 * semantics as the API's `GraphStore` (aggregation, traversal, search ranking, paths, code reads),
 * so the app behaves the same against fixtures as against a real snapshot.
 */

import { EDGE_KINDS } from "../types";
import type {
  AggregatedEdge,
  ApiNode,
  CodeSlice,
  Direction,
  Edge,
  EdgeKind,
  EdgeLinks,
  GraphPath,
  Hop,
  Neighborhood,
  NodeDetail,
  NodeKind,
  Overview,
  Owner,
  Ranked,
  SubgraphBody,
  SubgraphLevel,
  SymbolMatch,
} from "../types";

export interface FixtureData {
  nodes: ApiNode[];
  edges: Edge[];
  /** File text by path; files without text behave like skipped files. */
  files?: Record<string, string>;
  /** Computes a file's text on demand (large generated fixtures). */
  fileText?: (path: string) => string | undefined;
  owners?: (path: string) => Owner[];
}

/** An API error to answer with: HTTP status, stable code, message, and details. */
export class MockApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly headers: Record<string, string> = {},
  ) {
    super(message);
  }
}

export const MAX_READ_LINES = 400;
export const MAX_NEIGHBORS = 1000;
export const MAX_DEPTH = 20;
export const MAX_SEARCH_LIMIT = 50;
export const MAX_LINKS = 200;
export const CORE_MIN_CONFIDENCE = 0.6;
const LOCATABLE: ReadonlySet<NodeKind> = new Set(["class", "function", "method", "test"]);
const KIND_RANK: Record<string, number> = { method: 0, function: 1, test: 2 };
const EDGE_CODE = new Map(EDGE_KINDS.map((kind, index) => [kind, index]));

function invalid(message: string): MockApiError {
  return new MockApiError(422, "invalid_request", message);
}

function notFound(resource: string, message: string): MockApiError {
  return new MockApiError(404, "not_found", message, { resource });
}

function byKey(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function edgeOrder(a: Edge, b: Edge): number {
  return (
    byKey(a.src, b.src) ||
    byKey(a.dst, b.dst) ||
    (EDGE_CODE.get(a.kind) ?? 0) - (EDGE_CODE.get(b.kind) ?? 0)
  );
}

/** pg_trgm's similarity: shared trigrams of the padded, lower-cased words over all trigrams. */
export function trigramSimilarity(a: string, b: string): number {
  const grams = (text: string) => {
    const set = new Set<string>();
    for (const word of text.toLowerCase().split(/[^\p{L}\p{N}]+/u)) {
      if (!word) continue;
      const padded = `  ${word} `;
      for (let i = 0; i + 3 <= padded.length; i += 1) set.add(padded.slice(i, i + 3));
    }
    return set;
  };
  const left = grams(a);
  const right = grams(b);
  if (left.size === 0 || right.size === 0) return 0;
  let shared = 0;
  for (const gram of left) if (right.has(gram)) shared += 1;
  return shared / (left.size + right.size - shared);
}

function lines(text: string): string[] {
  const parts = text.split("\n");
  if (parts.length > 1 && parts[parts.length - 1] === "") parts.pop();
  return parts;
}

export class MockGraph {
  readonly nodes = new Map<string, ApiNode>();
  private readonly children = new Map<string, string[]>();
  private readonly outgoing = new Map<string, Edge[]>();
  private readonly incoming = new Map<string, Edge[]>();
  private readonly filesByPath = new Map<string, ApiNode>();
  private readonly memo = new Map<string, SubgraphBody>();

  constructor(private readonly data: FixtureData) {
    const sorted = [...data.nodes].sort((a, b) => byKey(a.key, b.key));
    for (const node of sorted) {
      this.nodes.set(node.key, node);
      if (node.kind === "file") this.filesByPath.set(node.path, node);
    }
    for (const node of sorted) {
      if (node.parent_key && this.nodes.has(node.parent_key)) {
        const list = this.children.get(node.parent_key) ?? [];
        list.push(node.key);
        this.children.set(node.parent_key, list);
      }
    }
    for (const edge of [...data.edges].sort(edgeOrder)) {
      if (!this.nodes.has(edge.src) || !this.nodes.has(edge.dst)) continue;
      (this.outgoing.get(edge.src) ?? this.outgoing.set(edge.src, []).get(edge.src)!).push(edge);
      (this.incoming.get(edge.dst) ?? this.incoming.set(edge.dst, []).get(edge.dst)!).push(edge);
    }
  }

  get nodeCount(): number {
    return this.nodes.size;
  }

  private node(key: string): ApiNode {
    const found = this.nodes.get(key);
    if (!found) throw notFound("node", `This snapshot has no node ${JSON.stringify(key)}.`);
    return found;
  }

  childrenOf(key: string): string[] {
    return this.children.get(key) ?? [];
  }

  private descendants(key: string): string[] {
    const out: string[] = [];
    const stack = [...this.childrenOf(key)].reverse();
    while (stack.length) {
      const next = stack.pop()!;
      out.push(next);
      const kids = this.childrenOf(next);
      for (let i = kids.length - 1; i >= 0; i -= 1) stack.push(kids[i]!);
    }
    return out;
  }

  /** Children of `parentKey` at `level` and the edges between them (GraphStore.subgraph). */
  subgraph(parentKey: string, level: SubgraphLevel): SubgraphBody {
    const memoKey = `${level}|${parentKey}`;
    const memo = this.memo.get(memoKey);
    if (memo) return memo;
    const parent = this.node(parentKey);
    if ((level === "dir" || level === "file") && parent.kind !== "dir") {
      throw invalid(`Level '${level}' needs a directory, not a ${parent.kind}.`);
    }
    if (level === "symbol" && parent.kind === "dir") {
      throw invalid("Level 'symbol' needs a file or a symbol, not a directory.");
    }
    const children =
      level === "file"
        ? this.descendants(parentKey)
            .filter((key) => this.nodes.get(key)!.kind === "file")
            .sort(byKey)
        : this.childrenOf(parentKey);
    const owner = new Map<string, number>();
    children.forEach((child, index) => {
      owner.set(child, index);
      for (const below of this.descendants(child)) owner.set(below, index);
    });
    const totals = new Map<string, [number, number, number, number, number, number]>();
    for (const [key, srcChild] of owner) {
      for (const edge of this.outgoing.get(key) ?? []) {
        if (edge.kind === "contains") continue;
        const dstChild = owner.get(edge.dst);
        if (dstChild === undefined || dstChild === srcChild) continue;
        const code = EDGE_CODE.get(edge.kind) ?? 0;
        const id = `${srcChild}|${dstChild}|${code}`;
        const total = totals.get(id);
        if (total) {
          total[3] += 1;
          total[4] = Math.min(total[4], edge.confidence);
          total[5] = Math.max(total[5], edge.confidence);
        } else {
          totals.set(id, [srcChild, dstChild, code, 1, edge.confidence, edge.confidence]);
        }
      }
    }
    const edges: AggregatedEdge[] = [...totals.values()]
      .sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2])
      .map(([src, dst, code, count, low, high]) => ({
        src: children[src]!,
        dst: children[dst]!,
        kind: EDGE_KINDS[code]!,
        count,
        min_confidence: low,
        max_confidence: high,
      }));
    const result: SubgraphBody = {
      parent,
      level,
      nodes: children.map((key) => this.nodes.get(key)!),
      edges,
    };
    this.memo.set(memoKey, result);
    return result;
  }

  /** The edges behind an aggregated edge (GraphStore.edge_links). */
  edgeLinks(src: string, dst: string, kind: EdgeKind, limit: number): EdgeLinks {
    if (limit < 1 || limit > MAX_LINKS) {
      throw invalid(`The limit must be between 1 and ${MAX_LINKS}.`);
    }
    this.node(src);
    this.node(dst);
    const targets = new Set([dst, ...this.descendants(dst)]);
    const found: Edge[] = [];
    for (const key of [src, ...this.descendants(src)]) {
      for (const edge of this.outgoing.get(key) ?? []) {
        if (edge.kind === kind && targets.has(edge.dst)) found.push(edge);
      }
    }
    // Strongest first; the API breaks ties by storage order, which the fixtures' edge order matches.
    const links = found
      .map((edge, index) => ({ edge, index }))
      .sort((a, b) => b.edge.confidence - a.edge.confidence || a.index - b.index)
      .slice(0, limit)
      .map(({ edge }) => edge);
    const ends = new Set(links.flatMap((edge) => [edge.src, edge.dst]));
    return {
      src,
      dst,
      kind,
      total: found.length,
      links,
      nodes: [...ends].sort(byKey).map((key) => this.nodes.get(key)!),
    };
  }

  private fileOf(key: string): string | null {
    let node = this.nodes.get(key);
    while (node && node.kind !== "file")
      node = node.parent_key ? this.nodes.get(node.parent_key) : undefined;
    return node ? node.key : null;
  }

  /** Where to start reading (GraphStore.overview). */
  overview(top = 8): Overview {
    const counts: Record<string, number> = {};
    for (const node of this.nodes.values()) counts[node.kind] = (counts[node.kind] ?? 0) + 1;
    const perFile = new Map<string, number>();
    const perSymbol = new Map<string, number>();
    const users = new Map<string, Set<string>>();
    const count = (target: string, from: string, counter: Map<string, number>) => {
      counter.set(target, (counter.get(target) ?? 0) + 1);
      (users.get(target) ?? users.set(target, new Set()).get(target)!).add(from);
    };
    for (const edges of this.outgoing.values()) {
      for (const edge of edges) {
        if (edge.kind !== "imports" && edge.kind !== "calls") continue;
        if (edge.confidence < CORE_MIN_CONFIDENCE) continue;
        const from = this.fileOf(edge.src);
        const to = this.fileOf(edge.dst);
        if (!from || !to || from === to) continue;
        count(to, from, perFile);
        if (this.nodes.get(edge.dst)!.kind !== "file") count(edge.dst, from, perSymbol);
      }
    }
    const ranked = (counter: Map<string, number>): Ranked[] =>
      [...counter]
        .sort((a, b) => b[1] - a[1] || byKey(a[0], b[0]))
        .slice(0, top)
        .map(([key, uses]) => ({ node: this.nodes.get(key)!, uses, files: users.get(key)!.size }));
    const all = [...this.nodes.values()].sort((a, b) => byKey(a.key, b.key));
    const isTestFile = (path: string) => this.filesByPath.get(path)?.meta?.is_test === true;
    const routes = all.filter((node) => node.kind === "route" && !isTestFile(node.path));
    const publicSymbols = all
      .filter(
        (node) => (node.kind === "class" || node.kind === "function") && node.meta?.public === true,
      )
      // Most used from other files first, so a library's main names lead (then by key).
      .sort(
        (a, b) => (perSymbol.get(b.key) ?? 0) - (perSymbol.get(a.key) ?? 0) || byKey(a.key, b.key),
      );
    const sourceFiles = new Set(
      all.filter((node) => node.kind === "file" && node.meta?.is_test !== true).map((n) => n.key),
    );
    const tested = new Set<string>();
    for (const edges of this.outgoing.values()) {
      for (const edge of edges) {
        const file = edge.kind === "tested_by" ? this.fileOf(edge.src) : null;
        if (file && sourceFiles.has(file)) tested.add(file);
      }
    }
    return {
      counts,
      public_symbols: publicSymbols.slice(0, top * 2),
      routes: routes.slice(0, top * 2),
      core_files: ranked(perFile),
      core_symbols: ranked(perSymbol),
      tested_files: tested.size,
      source_files: sourceFiles.size,
    };
  }

  private countByKind(edges: readonly Edge[]): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const kind of EDGE_KINDS) {
      const count = edges.filter((edge) => edge.kind === kind).length;
      if (count) counts[kind] = count;
    }
    return counts;
  }

  nodeDetail(key: string): NodeDetail {
    const node = this.node(key);
    return {
      node,
      owners: node.kind === "dir" ? [] : (this.data.owners?.(node.path) ?? []),
      degree: {
        incoming: this.countByKind(this.incoming.get(key) ?? []),
        outgoing: this.countByKind(this.outgoing.get(key) ?? []),
      },
    };
  }

  /** (edge, neighbor) pairs around a node in the API's order: out-edges kind by kind, then
   * in-edges kind by kind (SnapshotGraph._incident), so ties break the same way. */
  private incident(key: string, direction: Direction, kinds: ReadonlySet<EdgeKind>) {
    const found: { edge: Edge; neighbor: string }[] = [];
    if (direction !== "in") {
      const out = this.outgoing.get(key) ?? [];
      for (const kind of EDGE_KINDS) {
        if (!kinds.has(kind)) continue;
        for (const edge of out) if (edge.kind === kind) found.push({ edge, neighbor: edge.dst });
      }
    }
    if (direction !== "out") {
      const into = this.incoming.get(key) ?? [];
      for (const kind of EDGE_KINDS) {
        if (!kinds.has(kind)) continue;
        for (const edge of into) if (edge.kind === kind) found.push({ edge, neighbor: edge.src });
      }
    }
    return found;
  }

  neighbors(
    key: string,
    direction: Direction,
    kinds: readonly EdgeKind[] | null,
    depth: number,
  ): Neighborhood {
    const root = this.node(key);
    if (!Number.isInteger(depth) || depth < 1 || depth > MAX_DEPTH) {
      throw invalid(`The depth must be between 1 and ${MAX_DEPTH}.`);
    }
    const chosen = new Set<EdgeKind>(kinds && kinds.length ? kinds : EDGE_KINDS);
    const depths = new Map<string, number>([[key, 0]]);
    const followed = new Set<Edge>();
    const edges: Edge[] = [];
    let truncated = false;
    let frontier = [key];
    for (let level = 1; level <= depth; level += 1) {
      const reached: string[] = [];
      for (const node of frontier) {
        for (const { edge, neighbor } of this.incident(node, direction, chosen)) {
          if (!depths.has(neighbor)) {
            if (depths.size > MAX_NEIGHBORS) {
              truncated = true;
              continue;
            }
            depths.set(neighbor, level);
            reached.push(neighbor);
          }
          if (!followed.has(edge)) {
            followed.add(edge);
            edges.push(edge);
          }
        }
      }
      if (!reached.length) break;
      frontier = reached.sort(byKey);
    }
    const nodes: Hop[] = [...depths]
      .filter(([found]) => found !== key)
      .sort((a, b) => a[1] - b[1] || byKey(a[0], b[0]))
      .map(([found, hops]) => ({ node: this.nodes.get(found)!, depth: hops }));
    return {
      root,
      direction,
      kinds: EDGE_KINDS.filter((kind) => chosen.has(kind)),
      depth,
      nodes,
      edges: edges.sort(edgeOrder),
      truncated,
    };
  }

  private file(path: string): ApiNode {
    const file = this.filesByPath.get(path);
    if (!file) throw notFound("file", `${path} is not a file of this snapshot.`);
    return file;
  }

  private text(path: string): string | undefined {
    return this.data.files?.[path] ?? this.data.fileText?.(path);
  }

  locate(path: string, line: number): ApiNode {
    const file = this.file(path);
    if (!Number.isInteger(line) || line < 1) throw invalid("Lines start at 1.");
    const text = this.text(path);
    if (text !== undefined) {
      const count = lines(text).length;
      if (line > Math.max(count, 1)) {
        throw invalid(`Line ${line} is past the end of ${path} (${count} lines).`);
      }
    }
    const candidates = this.descendants(file.key)
      .map((key) => this.nodes.get(key)!)
      .filter(
        (node) =>
          LOCATABLE.has(node.kind) &&
          node.span &&
          node.span.start_line <= line &&
          node.span.end_line >= line,
      )
      .sort((a, b) => {
        const x = a.span!;
        const y = b.span!;
        return (
          y.start_line - x.start_line ||
          y.start_col - x.start_col ||
          x.end_line - y.end_line ||
          x.end_col - y.end_col ||
          (KIND_RANK[a.kind] ?? 3) - (KIND_RANK[b.kind] ?? 3) ||
          byKey(a.key, b.key)
        );
      });
    return candidates[0] ?? file;
  }

  search(query: string, kinds: readonly NodeKind[] | null, limit: number): SymbolMatch[] {
    const needle = query.trim();
    if (!needle) throw invalid("The search query is empty.");
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_SEARCH_LIMIT) {
      throw invalid(`The limit must be between 1 and ${MAX_SEARCH_LIMIT}.`);
    }
    const lower = needle.toLowerCase();
    const allowed = kinds && kinds.length ? new Set(kinds) : null;
    const hits: { node: ApiNode; rank: number; score: number }[] = [];
    for (const node of this.nodes.values()) {
      if (allowed && !allowed.has(node.kind)) continue;
      const name = node.name;
      const qualified = node.qualified_name;
      const score = Math.max(trigramSimilarity(name, needle), trigramSimilarity(qualified, needle));
      let rank: number;
      if (node.key === needle || name === needle || qualified === needle) rank = 0;
      else if (name.toLowerCase() === lower || qualified.toLowerCase() === lower) rank = 1;
      else if (name.toLowerCase().startsWith(lower) || qualified.toLowerCase().startsWith(lower))
        rank = 2;
      else if (score >= 0.3) rank = 3;
      else continue;
      hits.push({ node, rank, score });
    }
    hits.sort(
      (a, b) =>
        a.rank - b.rank ||
        b.score - a.score ||
        a.node.qualified_name.length - b.node.qualified_name.length ||
        byKey(a.node.key, b.node.key),
    );
    const matches: SymbolMatch["match"][] = ["exact", "exact", "prefix", "fuzzy"];
    return hits.slice(0, limit).map(({ node, rank, score }) => ({
      node,
      match: matches[rank]!,
      score: Math.round(score * 10_000) / 10_000,
    }));
  }

  path(from: string, to: string, kinds: readonly EdgeKind[] | null): GraphPath {
    const start = this.node(from);
    this.node(to);
    const chosen = new Set<EdgeKind>(kinds && kinds.length ? kinds : ["calls", "imports"]);
    const kindList = EDGE_KINDS.filter((kind) => chosen.has(kind));
    if (from === to) return { nodes: [start], edges: [], directed: true, kinds: kindList };
    for (const [direction, directed] of [
      ["out", true],
      ["both", false],
    ] as const) {
      const previous = new Map<string, { node: string; edge: Edge } | null>([[from, null]]);
      let frontier = [from];
      let found = false;
      while (frontier.length && !found) {
        const reached: string[] = [];
        for (const node of frontier) {
          for (const { edge, neighbor } of this.incident(node, direction, chosen)) {
            if (previous.has(neighbor)) continue;
            previous.set(neighbor, { node, edge });
            if (neighbor === to) {
              found = true;
              break;
            }
            reached.push(neighbor);
          }
          if (found) break;
        }
        frontier = reached;
      }
      if (!found) continue;
      const nodes: ApiNode[] = [];
      const edges: Edge[] = [];
      for (let at: string | undefined = to; at !== undefined;) {
        nodes.push(this.nodes.get(at)!);
        const step = previous.get(at);
        if (!step) break;
        edges.push(step.edge);
        at = step.node;
      }
      return { nodes: nodes.reverse(), edges: edges.reverse(), directed, kinds: kindList };
    }
    throw notFound("path", `No path connects ${from} and ${to} over ${kindList.join(", ")}.`);
  }

  code(path: string, start: number, end: number | null): CodeSlice {
    const file = this.file(path);
    const last = end ?? Math.max(start, 1) + MAX_READ_LINES - 1;
    if (start > last) throw invalid(`The range ${start}-${last} ends before it starts.`);
    const text = this.text(path);
    if (text === undefined) {
      const reason = String(file.meta.skip_reason ?? "no text");
      throw new MockApiError(
        404,
        "not_found",
        `${path} was skipped (${reason}); its text is not stored.`,
        {
          resource: "text",
        },
      );
    }
    const all = lines(text);
    const first = Math.max(start, 1);
    const stop = Math.min(last, all.length, first + MAX_READ_LINES - 1);
    if (first > stop) {
      throw invalid(`Line ${first} is past the end of ${path} (${all.length} lines).`);
    }
    return {
      path,
      blob_oid: String(file.meta.blob_oid ?? "0".repeat(40)),
      start: first,
      end: stop,
      text: all.slice(first - 1, stop).join("\n"),
      line_count: all.length,
    };
  }
}
