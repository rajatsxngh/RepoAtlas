"""Structure (GRA-2): ``dir``, ``file``, and symbol nodes, linked by ``contains`` edges.

Every directory holding an inventoried file gets a ``dir`` node (the root is ``dir:.``) and every
inventoried file a ``file`` node, skipped files included (with ``meta.skip_reason``); only parsed
files contribute symbol nodes. Symbols hang under their enclosing symbol, top-level ones under
their file.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import JsonValue

from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.keys import ROOT_DIR, ancestors, dir_key, file_key, parent_dir
from repoatlas.graph.model import EdgeKind, Node, NodeKind, Provenance
from repoatlas.ingest.inventory import FileEntry, Inventory
from repoatlas.parsing.models import ParsedFile, Symbol, TestCase


def build_structure(inventory: Inventory, parsed_files: Sequence[ParsedFile]) -> dict[str, Node]:
    """Directory, file, and symbol nodes; raises if a parse result has no indexed file."""
    nodes: dict[str, Node] = {}
    for entry in inventory.files:
        for directory in ancestors(entry.path):
            key = dir_key(directory)
            if key not in nodes:
                nodes[key] = _dir_node(directory)
        nodes[file_key(entry.path)] = _file_node(entry)
    if dir_key(ROOT_DIR) not in nodes:
        nodes[dir_key(ROOT_DIR)] = _dir_node(ROOT_DIR)

    indexed = {entry.path for entry in inventory.indexed}
    for parsed in parsed_files:
        if parsed.path not in indexed:
            raise ValueError(f"{parsed.path} was parsed but is not an indexed inventory file.")
        tests = {case.key: case for case in parsed.tests}
        keys = {symbol.key for symbol in parsed.symbols}
        for symbol in parsed.symbols:
            nodes[symbol.key] = _symbol_node(parsed, symbol, tests.get(symbol.key), keys)
    return nodes


def add_contains_edges(nodes: Iterable[Node], edges: EdgeSet) -> None:
    """``contains`` from every node's parent: dir → dir/file, file → symbol, symbol → member."""
    for node in nodes:
        if node.parent_key is not None:
            edges.add(node.parent_key, node.key, EdgeKind.CONTAINS, Provenance.TREE_SITTER, 1.0)


def _dir_node(path: str) -> Node:
    return Node(
        key=dir_key(path),
        kind=NodeKind.DIR,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        path=path,
        parent_key=None if path == ROOT_DIR else dir_key(parent_dir(path)),
    )


def _file_node(entry: FileEntry) -> Node:
    meta: dict[str, JsonValue] = {
        "language": entry.language,
        "is_test": entry.is_test,
        "blob_oid": entry.blob_oid,
    }
    if entry.loc is not None:
        meta["loc"] = entry.loc
    if entry.skip_reason is not None:
        meta["skip_reason"] = entry.skip_reason
    return Node(
        key=file_key(entry.path),
        kind=NodeKind.FILE,
        name=entry.path.rsplit("/", 1)[-1],
        qualified_name=entry.path,
        path=entry.path,
        parent_key=dir_key(parent_dir(entry.path)),
        meta=meta,
    )


def _symbol_node(parsed: ParsedFile, symbol: Symbol, test: TestCase | None, keys: set[str]) -> Node:
    meta: dict[str, JsonValue] = {"signature": symbol.signature}
    if symbol.decorators:
        meta["decorators"] = list(symbol.decorators)
    if symbol.modifiers:
        meta["modifiers"] = list(symbol.modifiers)
    if test is not None:
        meta["framework"] = test.framework
        meta["runner_id"] = test.runner_id
    parent = symbol.parent if symbol.parent in keys else None
    return Node(
        key=symbol.key,
        kind=NodeKind(symbol.kind),
        name=symbol.name,
        qualified_name=symbol.qualname,
        path=parsed.path,
        name_range=symbol.name_range,
        span=symbol.span,
        parent_key=parent or file_key(parsed.path),
        exported=symbol.exported,
        meta=meta,
    )
