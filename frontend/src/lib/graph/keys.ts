/**
 * Symbol keys (PRD §7.3): `dir:<path>`, `file:<path>`, `route:<path>:<METHOD> <pattern>`, and
 * `<lang>:<path>:<qualname>:<kind>[~<n>]`. `:`, `~`, and `%` inside a part are percent-encoded,
 * so splitting on `:` always finds the parts. Keys are opaque everywhere else in the app; these
 * helpers only derive what a key implies about its file and directories.
 */

export const ROOT_KEY = "dir:.";

export type ParsedKey =
  | { type: "dir"; path: string }
  | { type: "file"; path: string }
  | { type: "route"; path: string; signature: string }
  | { type: "symbol"; lang: string; path: string; qualname: string; kind: string; n: number };

const SYMBOL_LANGS = new Set(["py", "ts", "js"]);

function decodePart(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/** The parts of a key, with paths and names decoded; null when the key is malformed. */
export function parseKey(key: string): ParsedKey | null {
  const colon = key.indexOf(":");
  if (colon < 0) return null;
  const prefix = key.slice(0, colon);
  const rest = key.slice(colon + 1);
  if (prefix === "dir") return rest ? { type: "dir", path: decodePart(rest) } : null;
  if (prefix === "file") return rest ? { type: "file", path: decodePart(rest) } : null;
  const parts = rest.split(":");
  if (prefix === "route") {
    if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
    return { type: "route", path: decodePart(parts[0]), signature: decodePart(parts[1]) };
  }
  if (!SYMBOL_LANGS.has(prefix) || parts.length !== 3) return null;
  const [path, qualname, kindPart] = parts as [string, string, string];
  if (!path || !qualname || !kindPart) return null;
  const [kind, suffix] = kindPart.split("~", 2) as [string, string | undefined];
  const n = suffix === undefined ? 1 : Number.parseInt(suffix, 10);
  return {
    type: "symbol",
    lang: prefix,
    path: decodePart(path),
    qualname: decodePart(qualname),
    kind,
    n: Number.isFinite(n) ? n : 1,
  };
}

/** The encoded path part of a file, route, or symbol key (directories: their own path). */
function encodedPathPart(key: string): string | null {
  const colon = key.indexOf(":");
  if (colon < 0) return null;
  const prefix = key.slice(0, colon);
  const rest = key.slice(colon + 1);
  if (prefix === "dir" || prefix === "file") return rest || null;
  const end = rest.indexOf(":");
  return end > 0 ? rest.slice(0, end) : null;
}

/** The key of the file a route or symbol lives in; a file's own key; null for directories. */
export function fileKeyOf(key: string): string | null {
  if (key.startsWith("dir:")) return null;
  if (key.startsWith("file:")) return key;
  const path = encodedPathPart(key);
  return path ? `file:${path}` : null;
}

/** The directory key that holds an encoded repo-relative path. */
export function parentDirKey(encodedPath: string): string {
  const slash = encodedPath.lastIndexOf("/");
  return slash < 0 ? ROOT_KEY : `dir:${encodedPath.slice(0, slash)}`;
}

/**
 * Ancestors that follow from the key alone, nearest first, ending at the root: a symbol's
 * file and that file's directories. Enclosing classes or functions are not derivable (their kind
 * is not in the key); callers resolve those from loaded nodes.
 */
export function derivedAncestors(key: string): string[] {
  if (key === ROOT_KEY) return [];
  const chain: string[] = [];
  const file = fileKeyOf(key);
  let path: string | null;
  if (file && file !== key) {
    chain.push(file);
    path = encodedPathPart(file);
  } else {
    path = encodedPathPart(key);
  }
  while (path) {
    const dir = parentDirKey(path);
    chain.push(dir);
    if (dir === ROOT_KEY) break;
    path = dir.slice("dir:".length);
  }
  return chain;
}

/** One path segment for `/nodes/{key}`: `/` becomes `%2F`, `%` becomes `%25` (decoded once). */
export function encodeKeySegment(key: string): string {
  return encodeURIComponent(key);
}

const KEY_PREFIX = /^(dir|file|route|py|ts|js):/;

/** `"src|dst|kind"`, the edge key highlight layers use. */
export function edgeKey(src: string, dst: string, kind: string): string {
  return `${src}|${dst}|${kind}`;
}

/**
 * Splits an edge key. Kinds never hold `|`; between source and destination the split is the
 * first `|` followed by a key prefix, so paths containing `|` still parse.
 */
export function parseEdgeKey(value: string): { src: string; dst: string; kind: string } | null {
  const last = value.lastIndexOf("|");
  if (last <= 0) return null;
  const kind = value.slice(last + 1);
  const pair = value.slice(0, last);
  for (let index = pair.indexOf("|"); index > 0; index = pair.indexOf("|", index + 1)) {
    const src = pair.slice(0, index);
    const dst = pair.slice(index + 1);
    if (KEY_PREFIX.test(src) && KEY_PREFIX.test(dst)) return { src, dst, kind };
  }
  return null;
}
