from __future__ import annotations

import pytest

from repoatlas.parsing import ts_runtime
from repoatlas.parsing.models import Range


@pytest.mark.parametrize(
    ("path", "grammar"),
    [
        ("a.py", "python"),
        ("a.ts", "typescript"),
        ("a.mts", "typescript"),
        ("a.cts", "typescript"),
        ("App.TSX", "tsx"),
        ("a.js", "javascript"),
        ("a.mjs", "javascript"),
        ("a.cjs", "javascript"),
        ("a.jsx", "javascript"),
    ],
)
def test_grammar_for(path: str, grammar: str) -> None:
    assert ts_runtime.grammar_for(path) == grammar


@pytest.mark.parametrize(
    ("grammar", "source", "root"),
    [
        ("python", b"x = 1\n", "module"),
        ("typescript", b"const x: number = 1;\n", "program"),
        ("tsx", b"const el = <div className='a'>hi</div>;\n", "program"),
        ("javascript", b"const el = <div>hi</div>;\n", "program"),
    ],
)
def test_every_grammar_parses_cleanly(
    grammar: ts_runtime.Grammar, source: bytes, root: str
) -> None:
    tree = ts_runtime.parse(grammar, source)
    assert tree.root_node.type == root
    assert ts_runtime.collect_issues(tree.root_node) == ([], 0)


def test_ranges_are_one_based_lines_and_byte_columns() -> None:
    source = "x = 1\nnamé = 'ü'\n".encode()
    tree = ts_runtime.parse("python", source)
    second = tree.root_node.children[1]
    # "namé = 'ü'" is 10 characters but 12 UTF-8 bytes; columns count bytes.
    assert ts_runtime.node_range(second) == Range(start_line=2, start_col=0, end_line=2, end_col=12)
    assert ts_runtime.node_text(source, second) == "namé = 'ü'"


def test_syntax_errors_are_collected_and_capped() -> None:
    issues, total = ts_runtime.collect_issues(ts_runtime.parse("python", b"def f(:\n").root_node)
    assert total >= 1
    assert issues[0].kind in {"syntax_error", "missing_node"}

    broken = b"".join(b"x = (\n" if i % 2 else b"def (:\n" for i in range(60))
    issues, total = ts_runtime.collect_issues(ts_runtime.parse("python", broken).root_node)
    assert len(issues) <= ts_runtime.MAX_ISSUES_PER_FILE
    assert total >= len(issues)


def test_collapse() -> None:
    assert ts_runtime.collapse("def  f(\n    a,\n    b)") == "def f( a, b)"
    assert ts_runtime.collapse("x" * 300, limit=10) == "xxxxxxxxx…"


def test_grammar_versions_lists_all_packages() -> None:
    versions = ts_runtime.grammar_versions()
    assert set(versions) == set(ts_runtime.GRAMMAR_PACKAGES)
    assert versions["tree-sitter"] == "0.26.0"
