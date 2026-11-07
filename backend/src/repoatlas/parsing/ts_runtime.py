"""The only module that talks to tree-sitter directly (pinned versions; see pyproject).

Extractors use plain ``Node`` attributes (``type``, ``children``, ``child_by_field_name``, byte
offsets, points), which have been stable across releases; the query API is avoided entirely.

The binding stays on py-tree-sitter 0.25.2: 0.26.0 crashed the interpreter (an access violation on
Windows) partway through extracting real files such as flask's ``app.py``, and no unit test caught
it. Before upgrading any tree-sitter package, run ``scripts/smoke_parse.py`` as well as the tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache
from importlib.metadata import version
from typing import Literal

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Tree

from repoatlas.parsing.models import ParseIssue, Range

Grammar = Literal["python", "typescript", "tsx", "javascript"]
GRAMMAR_PACKAGES = (
    "tree-sitter",
    "tree-sitter-python",
    "tree-sitter-typescript",
    "tree-sitter-javascript",
)
MAX_ISSUES_PER_FILE = 20

_SUFFIX_GRAMMAR: dict[str, Grammar] = {
    ".py": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
}


@cache
def language(grammar: Grammar) -> Language:
    if grammar == "python":
        return Language(tree_sitter_python.language())
    if grammar == "typescript":
        return Language(tree_sitter_typescript.language_typescript())
    if grammar == "tsx":
        return Language(tree_sitter_typescript.language_tsx())
    return Language(tree_sitter_javascript.language())


def grammar_for(path: str) -> Grammar:
    name = path.rsplit("/", 1)[-1].lower()
    return _SUFFIX_GRAMMAR[name[name.rfind(".") :]]


def parse(grammar: Grammar, source: bytes) -> Tree:
    """Parse ``source``; parsers are cheap and not thread-safe, so each call gets its own."""
    return Parser(language(grammar)).parse(source)


def grammar_versions() -> dict[str, str]:
    """Installed tree-sitter package versions (part of the pipeline fingerprint)."""
    return {package: version(package) for package in GRAMMAR_PACKAGES}


def node_range(node: Node) -> Range:
    start, end = node.start_point, node.end_point
    return Range(
        start_line=start.row + 1, start_col=start.column, end_line=end.row + 1, end_col=end.column
    )


def node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def collapse(text: str, limit: int = 200) -> str:
    """Collapse whitespace runs to single spaces and cap the length."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def iter_issues(root: Node) -> Iterator[ParseIssue]:
    """Yield syntax errors and missing nodes, skipping subtrees that contain none."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_missing:
            yield ParseIssue(
                kind="missing_node", message=f"missing {node.type}", range=node_range(node)
            )
            continue
        if node.type == "ERROR":
            yield ParseIssue(kind="syntax_error", message="syntax error", range=node_range(node))
            continue
        if node.has_error:
            stack.extend(reversed(node.children))


def collect_issues(root: Node) -> tuple[list[ParseIssue], int]:
    """At most ``MAX_ISSUES_PER_FILE`` issues in source order, plus the total count."""
    issues = list(iter_issues(root))
    return issues[:MAX_ISSUES_PER_FILE], len(issues)
