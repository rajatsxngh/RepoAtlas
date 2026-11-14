"""The agent's tools (AGT-1, AGT-2): read-only questions about one snapshot.

Eight tools, each with a flat JSON schema for the model and an implementation over the graph
store, the retriever, and the impact engine:

| Tool | Arguments | Returns |
|---|---|---|
| ``search_code`` | ``query``, ``k`` (1 to 10) | Ranked code from hybrid retrieval |
| ``find_definition`` | ``name`` | Matching symbols with locations |
| ``find_callers`` | ``symbol_key``, ``depth`` (1 to 3) | Callers, edge confidence, call sites |
| ``find_callees`` | ``symbol_key``, ``depth`` (1 to 3) | Callees, edge confidence, call sites |
| ``trace_path`` | ``from_key``, ``to_key`` | A path of symbols and edges |
| ``read_file`` | ``path``, ``start``, ``end`` (at most 200 lines) | Code with line numbers |
| ``get_owners`` | ``path`` or ``symbol_key`` | Top authors with shares |
| ``get_impact`` | ``symbol_key``, ``limit`` (1 to 30) | Grouped impact items and unknown sites |

A ``Toolbox`` is bound to one snapshot when it is made; no tool takes a snapshot, a URL, or a
command, so a caller can only ever read that snapshot's stored data. ``call(name, arguments)``
checks the arguments against the tool's model and the allowlist helpers
(``repoatlas.agent.guard.check_arguments``) before anything runs and returns a
``ToolResult``: text for the caller, and ``EvidenceRef``s (the same references retrieval
returns) for every piece of code the text is about. A tool that can't run raises ``ToolError``
with a message the caller can act on.

Everything in a result's text comes from the repository (code, names, paths, author names) and
is untrusted data. The text is prepared for a model by ``repoatlas.agent.guard``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from repoatlas.agent.guard import check_arguments
from repoatlas.agent.results import ToolError, ToolResult
from repoatlas.graph.model import Edge, EdgeKind, Node, Owner
from repoatlas.impact.schema import GROUPS, ImpactParams, ImpactReport, SymbolTarget
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import Mode, RetrievalResult
from repoatlas.store.blobs import split_lines
from repoatlas.store.errors import PathNotInSnapshot, StoreError
from repoatlas.store.results import CodeSlice, GraphPath, Neighborhood, SymbolMatch

MAX_SEARCH_K = 10
DEFAULT_SEARCH_K = 5
MAX_DEPTH = 3
MAX_READ_LINES = 200
MAX_IMPACT_LIMIT = 30
DEFAULT_IMPACT_LIMIT = 15
MAX_DEFINITIONS = 10
MAX_EDGES_SHOWN = 40  # call edges listed by find_callers and find_callees
MAX_UNKNOWN_SHOWN = 15
MAX_OWNERS_SHOWN = 5
SYMBOL_LINES = 40  # lines of a symbol's body that a definition's evidence covers
OWNER_FILE_LINES = 20  # lines of the file that an ownership result's evidence covers
SEARCH_TOKENS = 4_000  # evidence budget of one search
MAX_TEXT_CHARS = 500
MAX_KEY_CHARS = 4_096


# ---------------------------------------------------------------------------
# What the tools read
# ---------------------------------------------------------------------------


class ToolStore(Protocol):
    """The reads the tools need from ``GraphStore``."""

    def find_symbols(
        self, snapshot_id: int, query: str, kinds: Iterable[str] | None = ..., limit: int = ...
    ) -> list[SymbolMatch]: ...

    def get_node(self, snapshot_id: int, key: str) -> Node | None: ...

    def callers(self, snapshot_id: int, key: str, depth: int = ...) -> Neighborhood: ...

    def callees(self, snapshot_id: int, key: str, depth: int = ...) -> Neighborhood: ...

    def shortest_path(
        self, snapshot_id: int, src: str, dst: str, kinds: Iterable[str] | None = ...
    ) -> GraphPath | None: ...

    def read_code(self, snapshot_id: int, path: str, start: int, end: int) -> CodeSlice: ...

    def owners(self, snapshot_id: int, path: str) -> list[Owner]: ...

    def blob_oids(self, snapshot_id: int, paths: Iterable[str]) -> dict[str, str]: ...


class ToolRetriever(Protocol):
    def retrieve(
        self,
        snapshot_id: int,
        query: str,
        mode: Mode = ...,
        budget_tokens: int = ...,
        k: int | None = ...,
    ) -> RetrievalResult: ...


class ImpactAnalyzer(Protocol):
    params: ImpactParams

    def analyze(
        self, snapshot_id: int, target: SymbolTarget, params: ImpactParams | None = ...
    ) -> ImpactReport: ...


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


class _Args(BaseModel):
    """Arguments are closed (no extra fields) and strictly typed: ``"3"`` is not a number."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


Text = Field(min_length=1, max_length=MAX_TEXT_CHARS)
Key = Field(min_length=1, max_length=MAX_KEY_CHARS)


class SearchCodeArgs(_Args):
    query: str = Text
    k: int = Field(default=DEFAULT_SEARCH_K, ge=1, le=MAX_SEARCH_K)


class FindDefinitionArgs(_Args):
    name: str = Text


class NeighborArgs(_Args):
    symbol_key: str = Key
    depth: int = Field(default=1, ge=1, le=MAX_DEPTH)


class TracePathArgs(_Args):
    from_key: str = Key
    to_key: str = Key


class ReadFileArgs(_Args):
    path: str = Key
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) is before start ({self.start})")
        return self


class GetOwnersArgs(_Args):
    path: str | None = Field(default=None, min_length=1, max_length=MAX_KEY_CHARS)
    symbol_key: str | None = Field(default=None, min_length=1, max_length=MAX_KEY_CHARS)

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.path is None) == (self.symbol_key is None):
            raise ValueError("give exactly one of path and symbol_key")
        return self


class GetImpactArgs(_Args):
    symbol_key: str = Key
    limit: int = Field(default=DEFAULT_IMPACT_LIMIT, ge=1, le=MAX_IMPACT_LIMIT)


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _integer(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _schema(properties: dict[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_KEY_HELP = (
    "A symbol key exactly as another tool returned it, for example "
    "py:src/shop/cart.py:Cart.total:method."
)


@dataclass(frozen=True)
class ToolSpec:
    """One tool: what the model is told, the model its arguments must fit, and its code."""

    name: str
    description: str
    input_schema: dict[str, Any]
    args: type[_Args]
    run: Callable[[Toolbox, Any], ToolResult]

    def definition(self) -> dict[str, Any]:
        """The tool in Messages API form."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# Evidence and text helpers
# ---------------------------------------------------------------------------


def numbered(text: str, first_line: int) -> str:
    """``text`` with its line numbers, as ``  12 | code``."""
    lines = split_lines(text) or [""]
    width = len(str(first_line + len(lines) - 1))
    return "\n".join(
        f"{number:>{width}} | {line}" for number, line in enumerate(lines, start=first_line)
    )


def location(ref: EvidenceRef) -> str:
    if ref.start_line == ref.end_line:
        return f"{ref.path} line {ref.start_line}"
    return f"{ref.path} lines {ref.start_line}-{ref.end_line}"


def node_ref(node: Node, oid: str | None, max_lines: int = SYMBOL_LINES) -> EvidenceRef | None:
    """The start of a symbol's body (at most ``max_lines`` lines); nothing for directories,
    files, and files whose blob is unknown."""
    where = node.span or node.name_range
    if oid is None or where is None:
        return None
    end = max(where.end_line, where.start_line)
    return EvidenceRef(
        blob_oid=oid,
        path=node.path,
        start_line=where.start_line,
        end_line=min(end, where.start_line + max_lines - 1),
        symbol_key=node.key,
    )


def site_ref(edge: Edge, nodes: dict[str, Node], oids: dict[str, str]) -> EvidenceRef | None:
    """Where an edge was seen: in the source's file, or for ``tested_by`` in the test's."""
    holder = nodes.get(edge.dst if edge.kind == EdgeKind.TESTED_BY else edge.src)
    if edge.site is None or holder is None or holder.path not in oids:
        return None
    return EvidenceRef(
        blob_oid=oids[holder.path],
        path=holder.path,
        start_line=edge.site.start_line,
        end_line=max(edge.site.end_line, edge.site.start_line),
        symbol_key=holder.key,
    )


def _describe(node: Node) -> str:
    where = node.span or node.name_range
    lines = f" lines {where.start_line}-{where.end_line}" if where is not None else ""
    return f"{node.key} ({node.kind.value} {node.qualified_name}, {node.path}{lines})"


def _edge_line(edge: Edge, ref: EvidenceRef | None) -> str:
    seen = f", seen at {location(ref)}" if ref is not None else ""
    return (
        f"- {edge.src} -[{edge.kind.value}, confidence {edge.confidence:.2f}, "
        f"{edge.provenance.value}]-> {edge.dst}{seen}"
    )


def _unique(refs: Iterable[EvidenceRef | None]) -> list[EvidenceRef]:
    found: dict[tuple[str, str, int, int], EvidenceRef] = {}
    for ref in refs:
        if ref is not None:
            found.setdefault((ref.path, ref.blob_oid, ref.start_line, ref.end_line), ref)
    return list(found.values())


# ---------------------------------------------------------------------------
# The toolbox
# ---------------------------------------------------------------------------


class Toolbox:
    """The tools, bound to one snapshot."""

    def __init__(
        self,
        snapshot_id: int,
        store: ToolStore,
        retriever: ToolRetriever,
        impact: ImpactAnalyzer,
        *,
        search_tokens: int = SEARCH_TOKENS,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.store = store
        self.retriever = retriever
        self.impact = impact
        self.search_tokens = search_tokens

    @property
    def specs(self) -> list[ToolSpec]:
        return list(TOOLS)

    def definitions(self) -> list[dict[str, Any]]:
        return tool_definitions()

    def parse(self, name: str, arguments: dict[str, Any]) -> tuple[ToolSpec, _Args]:
        """The tool and its checked arguments; ``ToolError`` for an unknown tool or arguments
        that don't fit. Nothing has run yet."""
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            known = ", ".join(TOOLS_BY_NAME)
            raise ToolError(f"There is no tool named {name[:80]!r}. Tools: {known}.", allowed=False)
        if not isinstance(arguments, dict):
            raise ToolError(f"{name} takes a JSON object of arguments.")
        try:
            return spec, spec.args.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
                for error in exc.errors(include_url=False, include_input=False)
            )
            raise ToolError(f"Invalid arguments for {name}: {problems}.") from None

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        spec, args = self.parse(name, arguments)
        check_arguments(args)
        try:
            return spec.run(self, args)
        except PathNotInSnapshot as exc:
            raise ToolError(str(exc), allowed=False) from None
        except StoreError as exc:
            raise ToolError(str(exc)) from None

    # --- helpers ---------------------------------------------------------------------------

    def _oids(self, nodes: Iterable[Node]) -> dict[str, str]:
        return self.store.blob_oids(self.snapshot_id, {node.path for node in nodes})

    def _node(self, key: str) -> Node:
        node = self.store.get_node(self.snapshot_id, key)
        if node is None:
            raise ToolError(
                f"The snapshot has no symbol with the key {key[:200]!r}. "
                "Use find_definition to look a symbol up by name."
            )
        return node

    # --- the tools -------------------------------------------------------------------------

    def search_code(self, args: SearchCodeArgs) -> ToolResult:
        found = self.retriever.retrieve(
            self.snapshot_id, args.query, mode="hybrid", budget_tokens=self.search_tokens, k=args.k
        )
        items = found.evidence[: args.k]
        if not items:
            return ToolResult(content="No code in the snapshot matched the query.")
        parts = [f"Results, best first: {len(items)}."]
        for rank, item in enumerate(items, start=1):
            symbol = f", in {item.ref.symbol_key}" if item.ref.symbol_key else ""
            parts.append(
                f"[{rank}] {location(item.ref)}{symbol} (score {item.score:.4f})\n"
                f"{numbered(item.text, item.ref.start_line)}"
            )
        return ToolResult(content="\n\n".join(parts), evidence=[item.ref for item in items])

    def find_definition(self, args: FindDefinitionArgs) -> ToolResult:
        matches = self.store.find_symbols(self.snapshot_id, args.name, limit=MAX_DEFINITIONS)
        if not matches:
            return ToolResult(content=f"No symbol, file, or directory matches {args.name!r}.")
        oids = self._oids(match.node for match in matches)
        lines = [f"Matches for {args.name!r}, best first: {len(matches)}."]
        refs: list[EvidenceRef | None] = []
        for match in matches:
            lines.append(f"- {_describe(match.node)}; {match.match} match")
            refs.append(node_ref(match.node, oids.get(match.node.path)))
        return ToolResult(
            content="\n".join(lines),
            evidence=_unique(refs),
            truncated=len(matches) == MAX_DEFINITIONS,
        )

    def _neighbors(self, found: Neighborhood, verb: str) -> ToolResult:
        root = found.root
        if not found.nodes:
            return ToolResult(content=f"Nothing {verb} {root.key} in the graph.")
        nodes = {root.key: root, **{hop.node.key: hop.node for hop in found.nodes}}
        oids = self._oids(nodes.values())
        lines = [f"Symbols {verb} {root.key}, up to {found.depth} calls away: {len(found.nodes)}."]
        lines.extend(f"- depth {hop.depth}: {_describe(hop.node)}" for hop in found.nodes)
        shown = found.edges[:MAX_EDGES_SHOWN]
        refs = [site_ref(edge, nodes, oids) for edge in shown]
        lines.append(f"Call edges ({len(shown)} of {len(found.edges)}):")
        lines.extend(_edge_line(edge, ref) for edge, ref in zip(shown, refs, strict=True))
        cut = found.truncated or len(shown) < len(found.edges)
        if cut:
            lines.append("The list is cut short: ask about a narrower symbol or a smaller depth.")
        return ToolResult(content="\n".join(lines), evidence=_unique(refs), truncated=cut)

    def find_callers(self, args: NeighborArgs) -> ToolResult:
        self._node(args.symbol_key)
        found = self.store.callers(self.snapshot_id, args.symbol_key, args.depth)
        return self._neighbors(found, "that call")

    def find_callees(self, args: NeighborArgs) -> ToolResult:
        self._node(args.symbol_key)
        found = self.store.callees(self.snapshot_id, args.symbol_key, args.depth)
        return self._neighbors(found, "called from")

    def trace_path(self, args: TracePathArgs) -> ToolResult:
        self._node(args.from_key)
        self._node(args.to_key)
        path = self.store.shortest_path(self.snapshot_id, args.from_key, args.to_key)
        if path is None:
            return ToolResult(
                content=f"The graph has no path between {args.from_key} and {args.to_key} "
                "over calls and imports."
            )
        if not path.edges:
            return ToolResult(content="Both keys name the same symbol.")
        nodes = {node.key: node for node in path.nodes}
        oids = self._oids(path.nodes)
        refs = [site_ref(edge, nodes, oids) for edge in path.edges]
        direction = (
            "following the edges' direction"
            if path.directed
            else "ignoring direction: no path follows the edges, so some point backwards"
        )
        lines = [f"A shortest path of {len(path.edges)} steps, {direction}."]
        lines.extend(f"- {_describe(node)}" for node in path.nodes)
        lines.append("Edges:")
        lines.extend(_edge_line(edge, ref) for edge, ref in zip(path.edges, refs, strict=True))
        ends = [node_ref(path.nodes[0], oids.get(path.nodes[0].path))]
        ends.append(node_ref(path.nodes[-1], oids.get(path.nodes[-1].path)))
        return ToolResult(content="\n".join(lines), evidence=_unique([*refs, *ends]))

    def read_file(self, args: ReadFileArgs) -> ToolResult:
        end = min(args.end, args.start + MAX_READ_LINES - 1)
        code = self.store.read_code(self.snapshot_id, args.path, args.start, end)
        cut = args.end > end and code.end < code.line_count
        note = (
            f" Only {MAX_READ_LINES} lines are returned per call; read on from line {code.end + 1}."
            if cut
            else ""
        )
        ref = EvidenceRef(
            blob_oid=code.blob_oid, path=code.path, start_line=code.start, end_line=code.end
        )
        header = f"{location(ref)} (the file has {code.line_count} lines).{note}"
        return ToolResult(
            content=f"{header}\n{numbered(code.text, code.start)}", evidence=[ref], truncated=cut
        )

    def get_owners(self, args: GetOwnersArgs) -> ToolResult:
        path = args.path if args.path is not None else self._node(args.symbol_key or "").path
        owners = self.store.owners(self.snapshot_id, path)
        if not owners:
            return ToolResult(content=f"No commit history is recorded for {path}.")
        shown = owners[:MAX_OWNERS_SHOWN]
        lines = [f"Authors of {path} by share of its commits ({len(owners)} in all):"]
        lines.extend(
            f"- {owner.author_name}: {owner.share:.0%} ({owner.commits} commits, "
            f"last on {owner.last_commit_at.date().isoformat()})"
            for owner in shown
        )
        refs: list[EvidenceRef] = []
        try:
            head = self.store.read_code(self.snapshot_id, path, 1, OWNER_FILE_LINES)
        except StoreError:
            pass  # a skipped or empty file has owners but no text to point at
        else:
            refs.append(
                EvidenceRef(
                    blob_oid=head.blob_oid, path=path, start_line=head.start, end_line=head.end
                )
            )
        return ToolResult(
            content="\n".join(lines), evidence=refs, truncated=len(shown) < len(owners)
        )

    def get_impact(self, args: GetImpactArgs) -> ToolResult:
        self._node(args.symbol_key)
        params = self.impact.params.model_copy(update={"limit": args.limit})
        report = self.impact.analyze(
            self.snapshot_id, SymbolTarget(symbol_key=args.symbol_key), params
        )
        stats = report.stats
        lines = [
            f"Changing {args.symbol_key} may affect {stats.items} symbols "
            f"({len(report.items)} listed, strongest first within each group)."
        ]
        refs: list[EvidenceRef | None] = []
        titles = {"test": "Tests", "public_api": "Public API", "caller": "Callers"}
        for group in GROUPS:
            items = report.group(group)
            if not items:
                continue
            lines.append(f"{titles[group]} ({len(items)}):")
            for item in items:
                steps = item.best_path
                via = " -> ".join([steps[0].src, *(step.dst for step in steps)]) if steps else "?"
                lines.append(
                    f"- {item.symbol_key} ({item.path}): score {item.score:.2f}, "
                    f"depth {item.depth}, via {via}"
                )
                refs.extend(item.evidence)
        unknown = report.unknown[:MAX_UNKNOWN_SHOWN]
        if unknown:
            lines.append(
                f"Unknown sites ({len(unknown)} of {len(report.unknown)}): places the graph "
                "could not resolve. They may depend on the symbol and are never safe to ignore."
            )
            for site in unknown:
                lines.append(f"- {location(site.site)}: `{site.callee_text}` ({site.reason})")
                refs.append(site.site)
        if not report.items and not unknown:
            lines.append("Nothing in the graph depends on it.")
        cut = stats.truncated or len(unknown) < len(report.unknown)
        return ToolResult(content="\n".join(lines), evidence=_unique(refs), truncated=cut)


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "search_code",
        "Search the repository's code by meaning and by keywords. Call this first for a new "
        "topic, and whenever you don't know which file or symbol holds something. Returns the "
        "best-matching pieces of code with their file, lines, and symbol key.",
        _schema(
            {
                "query": _string(
                    "What to look for: a description in plain words, or identifiers that "
                    "should appear in the code."
                ),
                "k": _integer(f"How many results to return, 1 to {MAX_SEARCH_K}. Default 5."),
            },
            ["query"],
        ),
        SearchCodeArgs,
        Toolbox.search_code,
    ),
    ToolSpec(
        "find_definition",
        "Look up symbols (functions, classes, methods, tests, routes) and files by name. Call "
        "this when you know a name and need its symbol key or where it is defined. Returns up "
        "to 10 matches, exact matches first, each with its key, kind, file, and lines.",
        _schema(
            {"name": _string("A name or qualified name, for example total or Cart.total.")},
            ["name"],
        ),
        FindDefinitionArgs,
        Toolbox.find_definition,
    ),
    ToolSpec(
        "find_callers",
        "List the symbols that call a symbol, directly or through other calls. Call this for "
        "questions like who uses this or what reaches this. Each call edge has a confidence "
        "(1.0 is certain, lower is a name-based guess) and the line of the call.",
        _schema(
            {
                "symbol_key": _string(_KEY_HELP),
                "depth": _integer(f"How many calls away to look, 1 to {MAX_DEPTH}. Default 1."),
            },
            ["symbol_key"],
        ),
        NeighborArgs,
        Toolbox.find_callers,
    ),
    ToolSpec(
        "find_callees",
        "List the symbols a symbol calls, directly or through other calls. Call this to follow "
        "what a function does step by step. Each call edge has a confidence and the line of "
        "the call.",
        _schema(
            {
                "symbol_key": _string(_KEY_HELP),
                "depth": _integer(f"How many calls away to look, 1 to {MAX_DEPTH}. Default 1."),
            },
            ["symbol_key"],
        ),
        NeighborArgs,
        Toolbox.find_callees,
    ),
    ToolSpec(
        "trace_path",
        "Find a shortest path between two symbols over calls and imports. Call this to check "
        "whether and how one symbol reaches another. Returns the symbols on the path and the "
        "edges between them, or says that there is no path.",
        _schema(
            {
                "from_key": _string(f"Where the path starts. {_KEY_HELP}"),
                "to_key": _string(f"Where the path ends. {_KEY_HELP}"),
            },
            ["from_key", "to_key"],
        ),
        TracePathArgs,
        Toolbox.trace_path,
    ),
    ToolSpec(
        "read_file",
        "Read lines of a file in the repository, with line numbers. Call this to see the code "
        "around a location another tool reported, or to read on past what a result showed. "
        f"Returns at most {MAX_READ_LINES} lines per call.",
        _schema(
            {
                "path": _string(
                    "The file's path from the repository root, with forward slashes, exactly "
                    "as another tool returned it."
                ),
                "start": _integer("The first line to read (lines start at 1)."),
                "end": _integer("The last line to read, inclusive."),
            },
            ["path", "start", "end"],
        ),
        ReadFileArgs,
        Toolbox.read_file,
    ),
    ToolSpec(
        "get_owners",
        "List the people who wrote most of a file, by share of its commits. Call this for "
        "questions about who owns, maintains, or knows a piece of code. Give either a file "
        "path or a symbol key (the symbol's file is used), not both.",
        _schema(
            {
                "path": _string("A file's path from the repository root."),
                "symbol_key": _string(_KEY_HELP),
            },
            [],
        ),
        GetOwnersArgs,
        Toolbox.get_owners,
    ),
    ToolSpec(
        "get_impact",
        "Predict what could break if a symbol changes: the tests, public API, and callers that "
        "depend on it, each with a score and the path that connects it, plus the places the "
        "graph could not resolve. Call this for questions about the effect or risk of changing "
        "something.",
        _schema(
            {
                "symbol_key": _string(_KEY_HELP),
                "limit": _integer(
                    f"How many affected symbols to list, 1 to {MAX_IMPACT_LIMIT}. Default 15."
                ),
            },
            ["symbol_key"],
        ),
        GetImpactArgs,
        Toolbox.get_impact,
    ),
)
TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}
TOOL_NAMES: tuple[str, ...] = tuple(TOOLS_BY_NAME)


def tool_definitions() -> list[dict[str, Any]]:
    """Every tool in Messages API form, in a fixed order (the order is part of the cached
    prompt prefix)."""
    return [spec.definition() for spec in TOOLS]


__all__ = ["TOOLS", "TOOL_NAMES", "ToolError", "ToolResult", "ToolSpec", "Toolbox"]
