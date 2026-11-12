"""Mutation engines (SBX-7): replace one function's body with a statement that raises.

The function is found by its symbol key (through ``parse_file``), so mutations and the graph
agree on names. Signatures and decorators stay as they were:

- Python (``ast``): the body becomes ``raise RuntimeError("repoatlas-mutation")``.
- TypeScript/JavaScript (tree-sitter): the body becomes
  ``{ throw new Error("repoatlas-mutation"); }``; an arrow function's expression body becomes
  that block.

Generators keep an unreachable ``yield`` so they stay generators (calling them still returns a
generator; iterating it raises). ``SkipTarget`` is raised for dunder methods, constructors,
abstract methods, overload declarations, functions under two lines, and anything whose
mutated source would not parse.
"""

from __future__ import annotations

import ast

from repoatlas.parsing.models import ParsedFile, Symbol
from repoatlas.parsing.symbols import parse_symbol_key
from repoatlas.parsing.ts_runtime import collect_issues, grammar_for, parse
from repoatlas.sandbox.parsed import parse_bytes
from repoatlas.sandbox.runner import Mutation

MARKER = "repoatlas-mutation"
PY_RAISE = f'raise RuntimeError("{MARKER}")'
TS_THROW = f'throw new Error("{MARKER}");'
MUTATION_KIND = "raise"

_OVERLOAD = frozenset({"overload", "typing.overload", "typing_extensions.overload"})
_TS_FUNCTIONS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "arrow_function",
        "function_expression",
        "function",
        "generator_function",
    }
)


class SkipTarget(ValueError):
    """This function is not mutated; the message says why."""


def skip_reason(symbol: Symbol) -> str | None:
    """Rules that need only the parsed symbol (shared with target sampling)."""
    if symbol.kind not in ("function", "method"):
        return f"not a function ({symbol.kind})"
    if symbol.name in ("__init__", "constructor"):
        return "constructor"
    if symbol.name.startswith("__") and symbol.name.endswith("__"):
        return "dunder method"
    if "abstract" in symbol.modifiers:
        return "abstract method"
    if any(text in _OVERLOAD for text in symbol.decorators):
        return "overload declaration"
    if symbol.span.end_line - symbol.span.start_line + 1 < 2:
        return "under 2 lines"
    return None


def _symbol(parsed: ParsedFile, symbol_key: str) -> Symbol:
    for symbol in parsed.symbols:
        if symbol.key == symbol_key:
            reason = skip_reason(symbol)
            if reason:
                raise SkipTarget(reason)
            return symbol
    raise SkipTarget("symbol not found in the file")


def _offset(lines: list[bytes], line: int, col: int) -> int:
    """Byte offset of 1-based ``line`` and 0-based byte ``col``."""
    return sum(len(text) for text in lines[: line - 1]) + col


# --------------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------------


def _py_function(tree: ast.Module, symbol: Symbol) -> ast.FunctionDef | ast.AsyncFunctionDef:
    # The outermost def with this name inside the symbol's span (spans may differ in trailing
    # comments, so only the start is compared).
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == symbol.name
        and symbol.span.start_line <= node.lineno <= symbol.span.end_line
    ]
    if not candidates:
        raise SkipTarget("function not found by ast")
    return min(candidates, key=lambda node: node.lineno)


def mutate_python(source: str, symbol_key: str) -> str:
    """``source`` with the body of ``symbol_key``'s function replaced by a ``raise``."""
    path = parse_symbol_key(symbol_key).path
    data = source.encode("utf-8")
    symbol = _symbol(parse_bytes(path, data), symbol_key)
    try:
        tree = ast.parse(data, filename=path)
    except SyntaxError as exc:
        raise SkipTarget(f"source does not parse: {exc.msg}") from exc
    node = _py_function(tree, symbol)
    first, last = node.body[0], node.body[-1]
    assert last.end_lineno is not None and last.end_col_offset is not None
    lines = data.splitlines(keepends=True)
    start = _offset(lines, first.lineno, first.col_offset)
    end = _offset(lines, last.end_lineno, last.end_col_offset)
    replacement = PY_RAISE + ("; yield" if "generator" in symbol.modifiers else "")
    mutated = data[:start] + replacement.encode() + data[end:]
    try:
        compile(mutated, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise SkipTarget(f"mutated source does not compile: {exc.msg}") from exc
    return mutated.decode("utf-8")


# --------------------------------------------------------------------------------------------
# TypeScript / JavaScript
# --------------------------------------------------------------------------------------------


def mutate_ts(source: str, symbol_key: str) -> str:
    """``source`` with the body of ``symbol_key``'s function replaced by a ``throw``."""
    path = parse_symbol_key(symbol_key).path
    data = source.encode("utf-8")
    symbol = _symbol(parse_bytes(path, data), symbol_key)
    grammar = grammar_for(path)
    root = parse(grammar, data).root_node
    lines = data.splitlines(keepends=True)
    span_start = _offset(lines, symbol.span.start_line, symbol.span.start_col)
    span_end = _offset(lines, symbol.span.end_line, symbol.span.end_col)

    stack = [root]
    function = None
    while stack:
        node = stack.pop()
        if node.end_byte <= span_start or node.start_byte >= span_end:
            continue
        if node.type in _TS_FUNCTIONS and node.start_byte >= span_start:
            function = node
            break
        stack.extend(reversed(node.children))
    if function is None:
        raise SkipTarget("function not found by tree-sitter")
    body = function.child_by_field_name("body")
    if body is None:
        raise SkipTarget("overload declaration (no body)")
    generator = "generator" in symbol.modifiers or function.type.startswith("generator")
    block = "{ " + TS_THROW + (" yield;" if generator else "") + " }"
    mutated = data[: body.start_byte] + block.encode() + data[body.end_byte :]
    _, before = collect_issues(root)
    _, after = collect_issues(parse(grammar, mutated).root_node)
    if after > before:
        raise SkipTarget("mutated source does not parse")
    return mutated.decode("utf-8")


def mutate_file(path: str, data: bytes, symbol_key: str) -> Mutation:
    """The ``Mutation`` (file replacement) for one target."""
    if parse_symbol_key(symbol_key).path != path:
        raise ValueError(f"{symbol_key} is not in {path}")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkipTarget("file is not UTF-8") from exc
    engine = mutate_python if path.endswith(".py") else mutate_ts
    mutated = engine(source, symbol_key)
    return Mutation(
        symbol_key=symbol_key, path=path, kind=MUTATION_KIND, content=mutated.encode("utf-8")
    )
