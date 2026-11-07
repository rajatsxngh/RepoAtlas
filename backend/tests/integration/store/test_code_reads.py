"""GraphStore.locate and read_code (T6): innermost symbols, clamping, snapshot files only."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from repoatlas.graph.model import CodeGraph, Node, NodeKind
from repoatlas.store.blobs import split_lines
from repoatlas.store.errors import InvalidQuery, PathNotInSnapshot, TextNotStored
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import save_snapshot
from tests.integration.store.conftest import Stored
from tests.integration.store.helpers import FINGERPRINT, REPOS, SHA_B, build_files, repo_meta

LOCATABLE = {NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.TEST}
PREFERENCE = {NodeKind.METHOD: 0, NodeKind.FUNCTION: 1, NodeKind.TEST: 2, NodeKind.CLASS: 3}


def innermost(graph: CodeGraph, path: str, line: int) -> str:
    """The node locate should return, straight from the definition."""
    candidates = [
        node
        for node in graph.nodes.values()
        if node.path == path
        and node.kind in LOCATABLE
        and node.span is not None
        and node.span.start_line <= line <= node.span.end_line
    ]
    if not candidates:
        return f"file:{path}"

    def order(node: Node) -> tuple[int, int, int, int, int, str]:
        span = node.span
        assert span is not None
        return (
            -span.start_line,
            -span.start_col,
            span.end_line,
            span.end_col,
            PREFERENCE[node.kind],
            node.key,
        )

    return min(candidates, key=order).key


def source_lines(fixture: str, path: str) -> list[str]:
    return split_lines((REPOS / fixture / path).read_bytes().decode("utf-8"))


def test_locate_examples(stored: Stored) -> None:
    def locate(path: str, line: int) -> str:
        return stored.store.locate(stored.py, path, line).key

    cart = "src/shop/cart.py"
    assert locate(cart, 15) == "py:src/shop/cart.py:Cart.add:method"
    assert locate(cart, 9) == "py:src/shop/cart.py:Cart:class"  # inside the class, between methods
    assert locate(cart, 29) == "py:src/shop/cart.py:GiftCart.add:method"
    assert locate(cart, 1) == "file:src/shop/cart.py"
    helpers = "src/shop/utils/helpers.py"
    assert locate(helpers, 10) == "py:src/shop/utils/helpers.py:format_price.with_symbol:function"
    assert locate(helpers, 11) == "py:src/shop/utils/helpers.py:format_price:function"
    assert locate("src/shop/app.py", 9) == "py:src/shop/app.py:get_cart:function"  # decorator line
    assert locate("tests/test_cart.py", 13) == (
        "py:tests/test_cart.py:TestGiftCart.test_gifts_are_free:test"
    )
    assert (
        stored.store.locate(stored.py, cart, 15)
        == stored.graphs[stored.py].nodes["py:src/shop/cart.py:Cart.add:method"]
    )


def test_locate_every_line_of_every_file(stored: Stored) -> None:
    for snapshot_id, built in stored.built.items():
        for entry in built.inventory.indexed:
            count = len(split_lines(built.blobs[entry.blob_oid].decode("utf-8")))
            for line in range(1, count + 1):
                found = stored.store.locate(snapshot_id, entry.path, line)
                assert found.key == innermost(built.graph, entry.path, line), (entry.path, line)


def test_locate_rejects_lines_outside_the_file_and_unknown_paths(stored: Stored) -> None:
    store = stored.store
    count = len(source_lines("py_small", "src/shop/cart.py"))
    assert store.locate(stored.py, "src/shop/cart.py", count).kind != NodeKind.DIR
    with pytest.raises(InvalidQuery, match="past the end"):
        store.locate(stored.py, "src/shop/cart.py", count + 1)
    with pytest.raises(InvalidQuery):
        store.locate(stored.py, "src/shop/cart.py", 0)
    for path in ("src/shop/nope.py", "pyproject.toml", "src/shop", "../src/shop/cart.py"):
        with pytest.raises(PathNotInSnapshot):
            store.locate(stored.py, path, 1)
    assert store.locate(stored.py, "vendor/lib.py", 3).key == "file:vendor/lib.py"  # skipped


def test_read_code_returns_the_exact_lines(stored: Stored) -> None:
    lines = source_lines("py_small", "src/shop/cart.py")
    code = stored.store.read_code(stored.py, "src/shop/cart.py", 8, 12)
    assert (code.path, code.start, code.end, code.line_count) == (
        "src/shop/cart.py",
        8,
        12,
        len(lines),
    )
    assert code.text == "\n".join(lines[7:12])
    blob = next(e.blob_oid for e in stored.built[stored.py].inventory.files if e.path == code.path)
    assert code.blob_oid == blob
    one = stored.store.read_code(stored.py, "src/shop/cart.py", 3, 3)
    assert one.text == lines[2] and (one.start, one.end) == (3, 3)
    whole = stored.store.read_code(stored.py, "src/shop/cart.py", 1, len(lines))
    assert whole.text + "\n" == (REPOS / "py_small" / "src/shop/cart.py").read_text(
        encoding="utf-8"
    )


def test_read_code_clamps_and_caps(stored: Stored, pg_engine: Engine) -> None:
    store = stored.store
    lines = source_lines("py_small", "src/shop/cart.py")
    clamped = store.read_code(stored.py, "src/shop/cart.py", -5, 10_000)
    assert (clamped.start, clamped.end) == (1, len(lines))
    small = GraphStore(pg_engine, store.cache, max_read_lines=5)
    capped = small.read_code(stored.py, "src/shop/cart.py", 3, 100)
    assert (capped.start, capped.end) == (3, 7) and capped.text == "\n".join(lines[2:7])
    with pytest.raises(InvalidQuery, match="past the end"):
        store.read_code(stored.py, "src/shop/cart.py", len(lines) + 1, len(lines) + 5)
    with pytest.raises(InvalidQuery, match="ends before"):
        store.read_code(stored.py, "src/shop/cart.py", 9, 3)


def test_read_code_only_reads_files_in_the_snapshot(stored: Stored) -> None:
    store = stored.store
    for path in ("pyproject.toml", "README.md", "src/shop/missing.py", "/etc/passwd", ""):
        with pytest.raises(PathNotInSnapshot):
            store.read_code(stored.py, path, 1, 5)
    with pytest.raises(PathNotInSnapshot):  # a file of another snapshot
        store.read_code(stored.ts, "src/shop/cart.py", 1, 5)
    with pytest.raises(TextNotStored, match="vendored"):
        store.read_code(stored.py, "vendor/lib.py", 1, 5)
    with pytest.raises(TextNotStored, match="declaration"):
        store.read_code(stored.py, "stubs/shop.pyi", 1, 5)


def test_read_code_keeps_line_endings_and_marks_nothing_extra(
    stored: Stored, pg_engine: Engine
) -> None:
    files = {
        "crlf.py": b"def f():\r\n    return 1\r\n",
        "lossy.py": b"x = '\xff'\ny = 2\n",
        "no_newline.py": b"a = 1\nb = 2",
        "empty.py": b"",
    }
    built = build_files(files)
    snapshot_id = save_snapshot(
        repo_meta("acme", "endings"),
        SHA_B,
        FINGERPRINT,
        built.inventory,
        built.blobs,
        built.graph,
        engine=pg_engine,
    )
    store = stored.store
    assert store.read_code(snapshot_id, "crlf.py", 1, 2).text == "def f():\r\n    return 1\r"
    assert store.read_code(snapshot_id, "lossy.py", 1, 1).text == "x = '�'"
    tail = store.read_code(snapshot_id, "no_newline.py", 2, 9)
    assert (tail.text, tail.end, tail.line_count) == ("b = 2", 2, 2)
    with pytest.raises(InvalidQuery, match="past the end"):
        store.read_code(snapshot_id, "empty.py", 1, 1)
    assert store.locate(snapshot_id, "empty.py", 1).key == "file:empty.py"
    assert store.locate(snapshot_id, "crlf.py", 2).key == "py:crlf.py:f:function"
