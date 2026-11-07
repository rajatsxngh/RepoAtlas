/**
 * A repository whose names and text are HTML and script payloads: directory and file names,
 * symbol and test names, route patterns, owner names, comments, docstrings, and strings. Every
 * one of them must show up as inert text (PRD §11).
 */

import type { ApiNode, Edge, NodeKind, Range } from "../../types";
import type { FixtureData } from "../graph";
import { syntheticOwners } from "../owners";

export const XSS_PAYLOADS = {
  dir: "<img src=x onerror=alert('dir')>",
  file: "<svg onload=alert('file')>.py",
  tsFile: '"><svg onload=alert(1)>.ts',
  test: "<img src=x onerror=alert('test')> renders",
  route: "/<script>alert('route')</script>",
  owner: "&lt;img src=x onerror=alert('owner')&gt; & \"Ra\" Lima",
  comment: "<script>alert('comment')</script>",
  docstring: "<b onmouseover=alert('docstring')>hover</b>",
} as const;

const DIR = XSS_PAYLOADS.dir;
const PY = `${DIR}/${XSS_PAYLOADS.file}`;
const TS = XSS_PAYLOADS.tsFile;

const PY_TEXT = `# ${XSS_PAYLOADS.comment}
"""${XSS_PAYLOADS.docstring}"""

from fastapi import FastAPI

app = FastAPI()


@app.get("${XSS_PAYLOADS.route}")
def render(name="<iframe src=javascript:alert('default')>"):
    """Returns <a href="javascript:alert('link')">a link</a>."""
    return helper("</textarea><script>alert('string')</script>")


def helper(text):
    # &lt;not an entity&gt; &amp; <!-- comment -->
    return text
`;

const TS_TEXT = `// ${XSS_PAYLOADS.comment}
/** ${XSS_PAYLOADS.docstring} */
export function show(value: string): string {
  return \`<div onclick="alert('template')">\${value}</div>\`;
}

describe("${XSS_PAYLOADS.test}", () => {
  it("${XSS_PAYLOADS.test}", () => {
    show("<script>alert('test body')</script>");
  });
});
`;

function range(start: number, end: number, endCol = 1): Range {
  return { start_line: start, start_col: 0, end_line: end, end_col: endCol };
}

function encodePart(text: string): string {
  return text.replace(/%/g, "%25").replace(/:/g, "%3A").replace(/~/g, "%7E");
}

function node(
  key: string,
  kind: NodeKind,
  name: string,
  path: string,
  parent: string | null,
  extra: Partial<ApiNode> = {},
): ApiNode {
  return {
    key,
    kind,
    name,
    qualified_name: extra.qualified_name ?? name,
    path,
    name_range: extra.name_range ?? null,
    span: extra.span ?? null,
    parent_key: parent,
    exported: extra.exported ?? false,
    meta: extra.meta ?? {},
  };
}

function edge(src: string, dst: string, kind: Edge["kind"], confidence = 1): Edge {
  return { src, dst, kind, provenance: "tree_sitter", confidence, site: null, meta: {} };
}

export function xssFixture(): FixtureData {
  const dirKey = `dir:${encodePart(DIR)}`;
  const pyKey = `file:${encodePart(PY)}`;
  const tsKey = `file:${encodePart(TS)}`;
  const render = `py:${encodePart(PY)}:render:function`;
  const helper = `py:${encodePart(PY)}:helper:function`;
  const route = `route:${encodePart(PY)}:GET ${encodePart(XSS_PAYLOADS.route)}`;
  const show = `ts:${encodePart(TS)}:show:function`;
  const testTitle = `${XSS_PAYLOADS.test} > ${XSS_PAYLOADS.test}`;
  const test = `ts:${encodePart(TS)}:${encodePart(testTitle)}:test`;
  const nodes: ApiNode[] = [
    node("dir:.", "dir", ".", ".", null),
    node(dirKey, "dir", DIR, DIR, "dir:."),
    node(pyKey, "file", XSS_PAYLOADS.file, PY, dirKey, {
      qualified_name: PY,
      meta: { language: "python", is_test: false, loc: 17, blob_oid: "a".repeat(40) },
    }),
    node(tsKey, "file", TS, TS, "dir:.", {
      qualified_name: TS,
      meta: { language: "typescript", is_test: true, loc: 11, blob_oid: "b".repeat(40) },
    }),
    node(render, "function", "render", PY, pyKey, {
      span: range(9, 12, 64),
      name_range: range(10, 10, 10),
      meta: { signature: `def render(name="<iframe src=javascript:alert('default')>")` },
    }),
    node(helper, "function", "helper", PY, pyKey, {
      span: range(15, 17, 15),
      name_range: range(15, 15, 10),
      meta: { signature: "def helper(text)" },
    }),
    node(route, "route", `GET ${XSS_PAYLOADS.route}`, PY, pyKey, {
      span: range(9, 9, 40),
      name_range: range(9, 9, 40),
      meta: { method: "GET", pattern: XSS_PAYLOADS.route, public: true },
    }),
    node(show, "function", "show", TS, tsKey, {
      span: range(3, 5, 1),
      name_range: range(3, 3, 20),
      exported: true,
      meta: { signature: "export function show(value: string): string" },
    }),
    node(test, "test", XSS_PAYLOADS.test, TS, tsKey, {
      qualified_name: testTitle,
      span: range(8, 10, 4),
      name_range: range(8, 8, 40),
      meta: { framework: "jest" },
    }),
  ];
  const edges: Edge[] = [
    ...nodes
      .filter((item) => item.parent_key)
      .map((item) => edge(item.parent_key!, item.key, "contains")),
    edge(render, helper, "calls"),
    edge(route, render, "routes_to"),
    edge(test, show, "calls", 0.5),
    edge(show, test, "tested_by", 0.5),
    edge(tsKey, pyKey, "imports", 0.4),
  ];
  return {
    nodes,
    edges,
    files: { [PY]: PY_TEXT, [TS]: TS_TEXT },
    owners: (path) => syntheticOwners(path, [XSS_PAYLOADS.owner, "Ana <b>bold</b> Lima"]),
  };
}
