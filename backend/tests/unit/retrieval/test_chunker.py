"""Chunk boundaries on the fixture repos and on synthetic files (RET-1, RET-2)."""

from __future__ import annotations

import itertools

import pytest

from repoatlas.graph.model import NodeKind
from repoatlas.retrieval.chunker import (
    ELIDED,
    MAX_CHUNK_TOKENS,
    OVERLAP_LINES,
    ChunkNode,
    chunk_file,
    chunk_graph,
    estimate_tokens,
    make_header,
)
from repoatlas.store.blobs import split_lines
from tests.unit.retrieval.helpers import by_key, fixture_chunks, fixture_graph, fixture_text

NAMES = ["py_small", "ts_small"]
OID = "1" * 40
FILE = ChunkNode(1, "file:big.py", "file", "file:big.py", 1, 1, 1)


@pytest.mark.parametrize("name", NAMES)
def test_every_function_method_and_test_gets_one_chunk_over_its_span(name: str) -> None:
    graph, chunks = fixture_graph(name), by_key(name)
    symbols = [
        node
        for node in graph.nodes.values()
        if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.TEST)
    ]
    assert symbols
    for node in symbols:
        assert node.span is not None
        [chunk] = chunks[node.key]
        assert (chunk.start_line, chunk.end_line) == (node.span.start_line, node.span.end_line)
        assert chunk.kind == node.kind.value
        source = split_lines(fixture_text(name, node.path) or "")
        assert chunk.text == "\n".join(source[chunk.start_line - 1 : chunk.end_line])
        assert chunk.ref.symbol_key == node.key


@pytest.mark.parametrize("name", NAMES)
def test_skipped_files_have_no_chunks_and_routes_none_of_their_own(name: str) -> None:
    graph = fixture_graph(name)
    skipped = {
        n.path for n in graph.nodes.values() if n.kind == "file" and n.meta.get("skip_reason")
    }
    chunks = fixture_chunks(name)
    assert skipped
    assert not {chunk.path for chunk in chunks} & skipped
    assert not any(chunk.node_key.startswith("route:") for chunk in chunks)


def test_class_chunks_hold_signatures_docstrings_and_member_signatures_only() -> None:
    [cart] = by_key("py_small")["py:src/shop/cart.py:Cart:class"]
    assert cart.kind == "class"
    assert (cart.start_line, cart.end_line) == (8, 25)
    lines = cart.text.split("\n")
    assert lines[0] == "class Cart:"
    assert '"""A shopping cart."""' in cart.text
    for signature in ("def __init__(self) -> None:", "def add(self, name: str, price: Decimal)"):
        assert signature in cart.text
    assert "@property" in cart.text  # decorators belong to the signature
    assert "self.items.append" not in cart.text  # no member bodies
    assert lines.count(f"        {ELIDED}") == 5

    [ts_cart] = by_key("ts_small")["ts:src/cart.ts:Cart:class"]
    assert "private items: Item[] = [];" in ts_cart.text  # class fields stay
    assert "this.items.push" not in ts_cart.text
    assert ts_cart.text.endswith("}")
    assert ts_cart.text.count("  }") >= 3  # each member's closing brace is kept


def test_module_chunks_hold_top_level_code_outside_symbols() -> None:
    [app] = by_key("py_small")["file:src/shop/app.py"]
    assert app.kind == "module"
    assert app.ref.symbol_key is None
    assert "app = FastAPI()" in app.text
    assert "def " not in app.text
    [server] = by_key("ts_small")["file:src/server.ts"]
    assert 'app.get("/carts/:id", getCart);' in server.text
    assert "async function getCart" not in server.text
    assert "\n\n\n" not in server.text


def test_long_import_blocks_collapse_to_one_line_per_module() -> None:
    source = (
        "from pkg.models import (\n"
        "    Alpha,\n"
        "    Beta,\n"
        ")\n"
        "\n"
        "import os\n"
        "import {\n"
        "  one,\n"
        "  two,\n"
        '} from "./mod";\n'
        "\n"
        "VALUE = 1\n"
    )
    [module] = chunk_file(path="m.py", blob_oid=OID, source=source, file_node=FILE, nodes=[])
    assert module.text.split("\n") == [
        "from pkg.models import ( Alpha, Beta, )",
        "import os",
        'import { one, two, } from "./mod";',
        "VALUE = 1",
    ]
    assert (module.start_line, module.end_line) == (1, 12)


def long_function(statements: int, width: int = 60) -> tuple[str, ChunkNode]:
    body = []
    for index in range(statements):
        body.append(f"    value_{index} = compute({index})  # " + "x" * width)
        if index % 7 == 3:
            body.append(
                f"    if value_{index}:\n        handle(\n            value_{index},\n        )"
            )
    source = "def big(a, b):\n" + "\n".join(body) + "\n    return a\n"
    count = len(split_lines(source))
    node = ChunkNode(2, "py:big.py:big:function", "function", "big", 1, count, 1, "def big(a, b)")
    return source, node


def test_long_functions_split_at_statement_boundaries_with_overlap() -> None:
    source, node = long_function(300)
    lines = split_lines(source)
    chunks = [
        chunk
        for chunk in chunk_file(
            path="big.py", blob_oid=OID, source=source, file_node=FILE, nodes=[node]
        )
        if chunk.node_key == node.key
    ]
    assert len(chunks) > 2
    assert [chunk.part for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert chunks[0].start_line == 1 and chunks[-1].end_line == len(lines)
    for chunk in chunks:
        assert chunk.tokens <= MAX_CHUNK_TOKENS
        assert chunk.header == chunks[0].header  # every part keeps the node's header
        assert chunk.text == "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
    for before, after in itertools.pairwise(chunks):
        assert after.start_line == before.end_line + 1 - OVERLAP_LINES
        cut = lines[before.end_line]  # the first line of the next statement
        assert cut.startswith("    ") and not cut.startswith("     ")  # body indentation
        assert not lines[before.end_line - 1].rstrip().endswith((",", "("))
    assert len({chunk.chunk_hash for chunk in chunks}) == len(chunks)


def test_short_functions_are_not_split() -> None:
    source, node = long_function(5)
    [chunk] = [
        c
        for c in chunk_file(
            path="big.py", blob_oid=OID, source=source, file_node=FILE, nodes=[node]
        )
        if c.node_key == node.key
    ]
    assert chunk.part == 0
    assert chunk.tokens == estimate_tokens(chunk.document)


def test_a_function_without_statement_boundaries_still_splits() -> None:
    source = "x = [\n" + "".join(f"    {index},  # {'y' * 70}\n" for index in range(600)) + "]\n"
    chunks = chunk_file(path="data.py", blob_oid=OID, source=source, file_node=FILE, nodes=[])
    assert len(chunks) > 1
    assert all(chunk.kind == "module" and chunk.tokens <= MAX_CHUNK_TOKENS for chunk in chunks)
    assert chunks[0].start_line == 1 and chunks[-1].end_line == 602
    assert [c.start_line for c in chunks] == sorted({c.start_line for c in chunks})


@pytest.mark.parametrize("name", NAMES)
def test_headers_come_from_the_chunks_own_file(name: str) -> None:
    paths = {node.path for node in fixture_graph(name).nodes.values() if node.kind == "file"}
    for chunk in fixture_chunks(name):
        header = chunk.header.split("\n")
        assert header[0] == chunk.path
        for other in paths - {chunk.path}:
            assert other not in chunk.header
        node = fixture_graph(name).nodes[chunk.node_key]
        if chunk.kind != "module":
            assert header[1] == node.qualified_name
            assert header[2] == " ".join(str(node.meta["signature"]).split())


def test_headers_join_path_name_and_signature() -> None:
    assert make_header("a.py", "A.f", "def f(\n    self)") == "a.py\nA.f\ndef f( self)"
    assert make_header("a.py", None, None) == "a.py"


def test_chunk_hashes_cover_header_and_text() -> None:
    source = "def f():\n    return 1\n"
    node = ChunkNode(2, "py:a.py:f:function", "function", "f", 1, 2, 1, "def f()")
    first = chunk_file(path="a.py", blob_oid=OID, source=source, file_node=FILE, nodes=[node])
    again = chunk_file(path="a.py", blob_oid="2" * 40, source=source, file_node=FILE, nodes=[node])
    moved = chunk_file(path="b.py", blob_oid=OID, source=source, file_node=FILE, nodes=[node])
    assert first[0].chunk_hash == again[0].chunk_hash  # the blob is not part of the hash
    assert first[0].chunk_hash != moved[0].chunk_hash  # the header (path) is


@pytest.mark.parametrize("name", NAMES)
def test_chunking_is_deterministic(name: str) -> None:
    graph = fixture_graph(name)
    first = chunk_graph(graph, lambda path: fixture_text(name, path))
    again = chunk_graph(graph, lambda path: fixture_text(name, path))
    assert first == again
    assert [chunk.chunk_hash for chunk in first] == [
        chunk.chunk_hash for chunk in fixture_chunks(name)
    ]


def test_empty_files_have_no_chunks() -> None:
    assert chunk_file(path="e.py", blob_oid=OID, source="", file_node=FILE, nodes=[]) == []
    assert chunk_file(path="e.py", blob_oid=OID, source="\n\n", file_node=FILE, nodes=[]) == []
