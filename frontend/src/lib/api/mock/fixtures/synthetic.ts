/**
 * A large generated snapshot for stress tests: a deep directory tree, many files, and dense
 * call graphs across them. Seeded, so the same options always give the same graph.
 */

import type { ApiNode, Edge, NodeKind, Range } from "../../types";
import type { FixtureData } from "../graph";
import { hash, syntheticOwners } from "../owners";

export interface SyntheticOptions {
  /** Total nodes, the root directory included. */
  nodes?: number;
  seed?: number;
}

export const SYNTHETIC_NODES = 10_000;

const WORDS = [
  "api",
  "auth",
  "billing",
  "cache",
  "cli",
  "core",
  "db",
  "events",
  "flags",
  "graph",
  "http",
  "io",
  "jobs",
  "kernel",
  "ledger",
  "mail",
  "models",
  "net",
  "orders",
  "parser",
  "queue",
  "render",
  "search",
  "store",
  "sync",
  "tasks",
  "users",
  "utils",
  "views",
  "web",
];
const VERBS = ["get", "load", "save", "parse", "render", "build", "check", "send", "sync", "merge"];

/** mulberry32: small, fast, and good enough for test data. */
export function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface SymbolPlan {
  key: string;
  name: string;
  kind: NodeKind;
  start: number;
  end: number;
  indent: number;
  calls: string[];
}

function span(start: number, end: number): Range {
  return { start_line: start, start_col: 0, end_line: end, end_col: 1 };
}

function dirNode(path: string, parent: string | null): ApiNode {
  return {
    key: path === "." ? "dir:." : `dir:${path}`,
    kind: "dir",
    name: path === "." ? "." : path.slice(path.lastIndexOf("/") + 1),
    qualified_name: path,
    path,
    name_range: null,
    span: null,
    parent_key: parent,
    exported: false,
    meta: {},
  };
}

export function syntheticGraph(options: SyntheticOptions = {}): FixtureData {
  const target = Math.max(50, options.nodes ?? SYNTHETIC_NODES);
  const random = seededRandom(options.seed ?? 20261108);
  const pick = <T>(items: readonly T[]): T => items[Math.floor(random() * items.length)]!;
  const between = (low: number, high: number) => low + Math.floor(random() * (high - low + 1));

  const nodes: ApiNode[] = [dirNode(".", null)];
  const edges: Edge[] = [];
  const contains = (src: string, dst: string) =>
    edges.push({
      src,
      dst,
      kind: "contains",
      provenance: "tree_sitter",
      confidence: 1,
      site: null,
      meta: {},
    });

  // Directories: about 4% of the nodes, grown depth-first most of the time so the tree is deep.
  const dirBudget = Math.max(8, Math.round(target * 0.04));
  const dirs: { path: string; key: string; depth: number }[] = [];
  const open: { path: string; key: string; depth: number }[] = [
    { path: ".", key: "dir:.", depth: 0 },
  ];
  const used = new Map<string, Set<string>>();
  while (dirs.length < dirBudget && open.length) {
    const index = random() < 0.7 ? open.length - 1 : 0;
    const parent = open[index]!;
    const taken = used.get(parent.key) ?? new Set<string>();
    used.set(parent.key, taken);
    let name = pick(WORDS);
    while (taken.has(name)) name = `${pick(WORDS)}_${between(2, 99)}`;
    taken.add(name);
    const path = parent.path === "." ? name : `${parent.path}/${name}`;
    const dir = { path, key: `dir:${path}`, depth: parent.depth + 1 };
    nodes.push(dirNode(path, parent.key));
    contains(parent.key, dir.key);
    dirs.push(dir);
    if (dir.depth < 8) open.push(dir);
    if (taken.size >= between(2, 6)) open.splice(index, 1);
  }
  const tests = { path: "tests", key: "dir:tests", depth: 1 };
  nodes.push(dirNode("tests", "dir:."));
  contains("dir:.", tests.key);
  dirs.push(tests);

  // Files: about 16% of the nodes, spread over the directories.
  const fileBudget = Math.max(dirs.length, Math.round(target * 0.16));
  const files: {
    key: string;
    path: string;
    lang: "py" | "ts";
    test: boolean;
    symbols: SymbolPlan[];
  }[] = [];
  for (let i = 0; files.length < fileBudget; i += 1) {
    const dir = i < dirs.length ? dirs[i]! : pick(dirs);
    const test = dir === tests;
    const lang: "py" | "ts" = dir.path.split("/")[0]!.length % 2 === 0 ? "ts" : "py";
    const stem = test ? `test_${pick(WORDS)}_${files.length}` : `${pick(WORDS)}_${files.length}`;
    const path = `${dir.path}/${stem}.${lang}`;
    const key = `file:${path}`;
    files.push({ key, path, lang, test, symbols: [] });
  }

  // Symbols fill the rest: functions, classes with methods, and tests in test files.
  let remaining = target - nodes.length - files.length;
  const allSymbols: { plan: SymbolPlan; file: number }[] = [];
  for (let round = 0; remaining > 0; round += 1) {
    for (let f = 0; f < files.length && remaining > 0; f += 1) {
      const file = files[f]!;
      if (round > 0 && random() < 0.5) continue;
      let line = (file.symbols.at(-1)?.end ?? 2) + 2;
      const prefix = `${file.lang}:${file.path}:`;
      if (file.test) {
        const name = `test_${pick(VERBS)}_${pick(WORDS)}_${round}_${file.symbols.length}`;
        const plan: SymbolPlan = {
          key: `${prefix}${name}:test`,
          name,
          kind: "test",
          start: line,
          end: line + 3,
          indent: 0,
          calls: [],
        };
        file.symbols.push(plan);
        allSymbols.push({ plan, file: f });
        remaining -= 1;
        continue;
      }
      if (random() < 0.3 && remaining >= 3) {
        const cls = `${pick(WORDS)[0]!.toUpperCase()}${pick(WORDS).slice(1)}${round}${file.symbols.length}`;
        const methods = Math.min(remaining - 1, between(2, 4));
        const plan: SymbolPlan = {
          key: `${prefix}${cls}:class`,
          name: cls,
          kind: "class",
          start: line,
          end: line + 1 + methods * 4,
          indent: 0,
          calls: [],
        };
        file.symbols.push(plan);
        allSymbols.push({ plan, file: f });
        remaining -= 1;
        for (let m = 0; m < methods; m += 1) {
          const name = `${pick(VERBS)}_${m}`;
          const start = line + 1 + m * 4;
          const method: SymbolPlan = {
            key: `${prefix}${cls}.${name}:method`,
            name,
            kind: "method",
            start,
            end: start + 3,
            indent: 4,
            calls: [],
          };
          file.symbols.push(method);
          allSymbols.push({ plan: method, file: f });
          remaining -= 1;
        }
        line = plan.end + 2;
      } else {
        const name = `${pick(VERBS)}_${pick(WORDS)}_${round}_${file.symbols.length}`;
        const plan: SymbolPlan = {
          key: `${prefix}${name}:function`,
          name,
          kind: "function",
          start: line,
          end: line + 3,
          indent: 0,
          calls: [],
        };
        file.symbols.push(plan);
        allSymbols.push({ plan, file: f });
        remaining -= 1;
      }
    }
  }

  for (const file of files) {
    const dirPath = file.path.slice(0, file.path.lastIndexOf("/"));
    const parent = `dir:${dirPath}`;
    const loc = (file.symbols.at(-1)?.end ?? 2) + 1;
    nodes.push({
      key: file.key,
      kind: "file",
      name: file.path.slice(file.path.lastIndexOf("/") + 1),
      qualified_name: file.path,
      path: file.path,
      name_range: null,
      span: null,
      parent_key: parent,
      exported: false,
      meta: {
        language: file.lang === "py" ? "python" : "typescript",
        is_test: file.test,
        loc,
        blob_oid: hash(file.path).toString(16).padStart(8, "0").repeat(5),
      },
    });
    contains(parent, file.key);
    let currentClass: string | null = null;
    for (const plan of file.symbols) {
      if (plan.kind === "class") currentClass = plan.key;
      else if (plan.kind !== "method") currentClass = null;
      const parentKey = plan.kind === "method" && currentClass ? currentClass : file.key;
      nodes.push({
        key: plan.key,
        kind: plan.kind,
        name: plan.name,
        qualified_name: plan.kind === "method" ? plan.key.split(":")[2]! : plan.name,
        path: file.path,
        name_range: span(plan.start, plan.start),
        span: span(plan.start, plan.end),
        parent_key: parentKey,
        exported: plan.kind !== "method" && random() < 0.4,
        meta: { signature: `${plan.kind === "class" ? "class" : "def"} ${plan.name}()` },
      });
      contains(parentKey, plan.key);
    }
  }

  // Dense calls: 2–5 per function, mostly to the same file or directory, some anywhere.
  const callable = allSymbols.filter(({ plan }) => plan.kind !== "class");
  const byFile = new Map<number, SymbolPlan[]>();
  for (const { plan, file } of callable) {
    const list = byFile.get(file) ?? [];
    list.push(plan);
    byFile.set(file, list);
  }
  const imports = new Set<string>();
  for (const { plan, file } of callable) {
    const count = between(2, 5);
    for (let c = 0; c < count; c += 1) {
      const roll = random();
      const local = byFile.get(file) ?? [];
      const neighbor =
        byFile.get(Math.min(files.length - 1, Math.max(0, file + between(-6, 6)))) ?? [];
      const pool = roll < 0.45 ? local : roll < 0.75 ? neighbor : [pick(callable).plan];
      const callee = pool.length ? pick(pool) : null;
      if (!callee || callee.key === plan.key || callee.kind === "test") continue;
      const confidence = roll < 0.75 ? 1 : random() < 0.5 ? 0.8 : 0.45;
      plan.calls.push(callee.name);
      edges.push({
        src: plan.key,
        dst: callee.key,
        kind: "calls",
        provenance: confidence === 1 ? "tree_sitter" : "name_match",
        confidence,
        site: span(plan.start + 1, plan.start + 1),
        meta: {},
      });
      const calleeFile = `file:${callee.key.split(":")[1]}`;
      const callerFile = files[file]!.key;
      if (calleeFile !== callerFile) imports.add(`${callerFile}|${calleeFile}`);
      if (plan.kind === "test") {
        edges.push({
          src: callee.key,
          dst: plan.key,
          kind: "tested_by",
          provenance: "import_resolver",
          confidence,
          site: null,
          meta: {},
        });
      }
    }
  }
  for (const pair of imports) {
    const [src, dst] = pair.split("|") as [string, string];
    edges.push({
      src,
      dst,
      kind: "imports",
      provenance: "import_resolver",
      confidence: 1,
      site: null,
      meta: {},
    });
  }

  const fileText = new Map(files.map((file) => [file.path, file]));
  return {
    nodes,
    edges,
    fileText: (path) => {
      const file = fileText.get(path);
      if (!file) return undefined;
      const loc = (file.symbols.at(-1)?.end ?? 2) + 1;
      const out = Array.from({ length: loc }, () => "");
      out[0] =
        file.lang === "py"
          ? `"""Generated module ${file.path}."""`
          : `// Generated module ${file.path}`;
      for (const plan of file.symbols) {
        const pad = " ".repeat(plan.indent);
        const head =
          plan.kind === "class"
            ? file.lang === "py"
              ? `class ${plan.name}:`
              : `export class ${plan.name} {`
            : file.lang === "py"
              ? `${pad}def ${plan.name}(self):`
              : `${pad}${plan.indent ? "" : "export function "}${plan.name}() {`;
        out[plan.start - 1] = head;
        if (plan.kind === "class") continue;
        plan.calls.slice(0, 2).forEach((callee, index) => {
          out[plan.start + index] =
            file.lang === "py" ? `${pad}    ${callee}()` : `${pad}  ${callee}();`;
        });
        out[plan.end - 1] = file.lang === "py" ? `${pad}    return None` : `${pad}}`;
      }
      return `${out.join("\n")}\n`;
    },
    owners: (path) => syntheticOwners(path),
  };
}
