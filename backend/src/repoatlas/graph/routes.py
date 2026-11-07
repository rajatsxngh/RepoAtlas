"""Routes (GRA-8): ``route`` nodes with ``routes_to`` edges to their handlers.

- FastAPI/Flask decorators (``@app.get("/x")``, ``@bp.route("/x", methods=[…])``) on a function:
  one route per method, handled by the decorated function (``tree_sitter``).
- Express calls (``app.get("/x", handler)``, ``router.post(…)``): the last argument is the
  handler. A named handler resolves through the enclosing scope (``tree_sitter`` in the same
  file, ``import_resolver`` through an import); an inline function becomes an ``<anon>``
  function node, and the calls inside it move from the enclosing scope to that node.
- Next.js: ``app/**/route.(ts|js)`` files exporting ``GET``/``POST``/… (route groups ``(x)`` and
  parallel slots ``@x`` are not part of the URL), and ``pages/api/**`` files with a default
  export (method ``ALL``). Handlers are linked by convention.

Route nodes are ``route:<path>:<METHOD> <pattern>``, hang under the file that declares them, and
carry ``meta.public = true``. Decorator paths must start with ``/``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.keys import file_key, route_key
from repoatlas.graph.model import EdgeKind, Node, NodeKind, Provenance
from repoatlas.graph.names import NameResolver
from repoatlas.graph.tiers import ROUTES_TO_CONFIDENCE
from repoatlas.graph.ts_resolver import TsModuleResolver
from repoatlas.parsing.models import ParsedFile, Range, RouteCandidate
from repoatlas.parsing.symbols import make_symbol_key

HTTP_EXPORTS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
ANON = "<anon>"
_DOTTED = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")
_NEXT_APP = re.compile(r"(?:^|/)app/((?:[^/]+/)*)route\.(?:ts|tsx|js|jsx|mjs|cjs|mts|cts)$")
_NEXT_PAGES = re.compile(r"(?:^|/)pages/api/(.+)\.(?:ts|tsx|js|jsx|mjs|cjs|mts|cts)$")

RangeKey = tuple[int, int, int, int]


def range_key(value: Range) -> RangeKey:
    return (value.start_line, value.start_col, value.end_line, value.end_col)


def _inside(inner: Range, outer: Range) -> bool:
    start = (inner.start_line, inner.start_col) >= (outer.start_line, outer.start_col)
    end = (inner.end_line, inner.end_col) <= (outer.end_line, outer.end_col)
    return start and end and range_key(inner) != range_key(outer)


@dataclass
class RouteResult:
    """Route and ``<anon>`` handler nodes, and the calls that moved into inline handlers."""

    nodes: dict[str, Node] = field(default_factory=dict)
    callers: dict[tuple[str, RangeKey], str] = field(default_factory=dict)


def add_routes(
    files: Sequence[ParsedFile],
    names: NameResolver,
    typescript: TsModuleResolver,
    existing: Mapping[str, Node],
    edges: EdgeSet,
) -> RouteResult:
    result = RouteResult()
    for parsed in files:
        calls = {range_key(call.range): call for call in parsed.calls}
        anon_count: dict[str, int] = {}
        for candidate in parsed.routes:
            if candidate.source == "decorator":
                _decorator_route(parsed, candidate, result, edges)
            else:
                route_call = calls.get(range_key(candidate.range))
                enclosing = route_call.enclosing if route_call is not None else None
                _call_route(
                    parsed, candidate, enclosing, names, existing, anon_count, result, edges
                )
        if parsed.language != "py":
            _nextjs_routes(parsed, typescript, existing, result, edges)
    return result


def _route_node(
    path: str,
    method: str,
    pattern: str,
    source: str,
    site: Range | None,
    extra: dict[str, JsonValue] | None = None,
) -> Node:
    meta: dict[str, JsonValue] = {
        "method": method,
        "pattern": pattern,
        "source": source,
        "public": True,
        **(extra or {}),
    }
    return Node(
        key=route_key(path, method, pattern),
        kind=NodeKind.ROUTE,
        name=f"{method} {pattern}",
        qualified_name=f"{method} {pattern}",
        path=path,
        name_range=site,
        span=site,
        parent_key=file_key(path),
        meta=meta,
    )


def _add_route(
    node: Node,
    handler: str | None,
    provenance: Provenance,
    site: Range | None,
    result: RouteResult,
    edges: EdgeSet,
) -> None:
    result.nodes.setdefault(node.key, node)
    if handler is not None:
        edges.add(
            node.key,
            handler,
            EdgeKind.ROUTES_TO,
            provenance,
            ROUTES_TO_CONFIDENCE,
            site=site,
        )


def _decorator_route(
    parsed: ParsedFile, candidate: RouteCandidate, result: RouteResult, edges: EdgeSet
) -> None:
    if not candidate.path.startswith("/"):
        return
    for method in candidate.methods:
        node = _route_node(parsed.path, method, candidate.path, "decorator", candidate.range)
        _add_route(node, candidate.handler, Provenance.TREE_SITTER, candidate.range, result, edges)


def _call_route(
    parsed: ParsedFile,
    candidate: RouteCandidate,
    enclosing: str | None,
    names: NameResolver,
    existing: Mapping[str, Node],
    anon_count: dict[str, int],
    result: RouteResult,
    edges: EdgeSet,
) -> None:
    handler: str | None = None
    provenance = Provenance.TREE_SITTER
    extra: dict[str, JsonValue] = {}
    if candidate.handler is None:
        handler = _inline_handler(parsed, candidate, enclosing, existing, anon_count, result)
    else:
        extra["handler"] = candidate.handler
        resolved = (
            names.resolve_chain(parsed.path, candidate.handler.split("."), enclosing)
            if _DOTTED.fullmatch(candidate.handler)
            else None
        )
        if resolved is not None and resolved.target.kind == "symbol" and resolved.target.key:
            handler = resolved.target.key
            if resolved.via != "definition" or resolved.target.path != parsed.path:
                provenance = Provenance.IMPORT_RESOLVER
    for method in candidate.methods:
        node = _route_node(parsed.path, method, candidate.path, "call", candidate.range, extra)
        _add_route(node, handler, provenance, candidate.range, result, edges)


def _inline_handler(
    parsed: ParsedFile,
    candidate: RouteCandidate,
    enclosing: str | None,
    existing: Mapping[str, Node],
    anon_count: dict[str, int],
    result: RouteResult,
) -> str:
    """An ``<anon>`` function node for an inline handler; calls inside it now come from it."""
    outer = existing.get(enclosing) if enclosing is not None else None
    qualname = f"{outer.qualified_name}.{ANON}" if outer is not None else ANON
    lang = parsed.language
    while True:
        anon_count[qualname] = anon_count.get(qualname, 0) + 1
        key = make_symbol_key(lang, parsed.path, qualname, "function", anon_count[qualname])
        if key not in existing and key not in result.nodes:
            break
    result.nodes[key] = Node(
        key=key,
        kind=NodeKind.FUNCTION,
        name=ANON,
        qualified_name=qualname,
        path=parsed.path,
        name_range=candidate.range,
        span=candidate.range,
        parent_key=enclosing if outer is not None else file_key(parsed.path),
        meta={"signature": candidate.text, "inline_handler": True},
    )
    for call in parsed.calls:
        if call.enclosing == enclosing and _inside(call.range, candidate.range):
            result.callers[(parsed.path, range_key(call.range))] = key
    return key


def nextjs_pattern(path: str) -> tuple[str, str] | None:
    """``(kind, URL pattern)`` for a Next.js route handler file, else ``None``."""
    app = _NEXT_APP.search(path)
    if app is not None:
        segments = [
            s
            for s in app.group(1).split("/")
            if s and not (s.startswith("(") and s.endswith(")")) and not s.startswith("@")
        ]
        return "nextjs_app", "/" + "/".join(segments)
    pages = _NEXT_PAGES.search(path)
    if pages is not None:
        segments = pages.group(1).split("/")
        if any(segment.startswith("_") for segment in segments):
            return None
        if segments[-1] == "index":
            segments = segments[:-1]
        return "nextjs_pages", "/".join(["/api", *segments])
    return None


def _nextjs_routes(
    parsed: ParsedFile,
    typescript: TsModuleResolver,
    existing: Mapping[str, Node],
    result: RouteResult,
    edges: EdgeSet,
) -> None:
    found = nextjs_pattern(parsed.path)
    if found is None:
        return
    source, pattern = found
    exported = typescript.exported_names(parsed.path)
    wanted = [(m, m) for m in HTTP_EXPORTS] if source == "nextjs_app" else [("ALL", "default")]
    for method, export in wanted:
        if export not in exported:
            continue
        target = typescript.lookup_export(parsed.path, export)
        handler = existing.get(target.key) if target.kind == "symbol" and target.key else None
        site = handler.name_range if handler is not None else None
        node = _route_node(parsed.path, method, pattern, source, site)
        key = handler.key if handler is not None else None
        _add_route(node, key, Provenance.CONVENTION, None, result, edges)
