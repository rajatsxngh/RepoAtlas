"""The unknown bucket (T3): unresolved calls and string literals naming the target."""

from __future__ import annotations

from functools import cache

from repoatlas.graph.model import CodeGraph, Node, NodeKind
from repoatlas.impact.data import MemoryImpactData, literal_pattern
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.schema import ImpactReport, SymbolTarget
from repoatlas.impact.unknown import unknown_sites
from tests.unit.graph.helpers import graph_of
from tests.unit.impact.helpers import MAIN, PARSE, TOKENIZE, project_data


@cache
def data() -> MemoryImpactData:
    return project_data()


def analyze(key: str, source: MemoryImpactData | None = None) -> ImpactReport:
    return ImpactEngine(source or data()).analyze(1, SymbolTarget(symbol_key=key))


def test_unresolved_calls_with_the_targets_name_are_unknown_sites() -> None:
    report = analyze(TOKENIZE)
    calls = [u for u in report.unknown if u.reason != "string_literal"]
    assert len(calls) == 1
    (site,) = calls
    assert site.callee_text == "obj._tokenize"
    assert site.reason == "not_found"
    assert (site.site.path, site.site.start_line, site.site.symbol_key) == ("pkg/cli.py", 6, MAIN)
    # main is unknown through this site, and not a ranked caller of _tokenize
    assert MAIN not in {i.symbol_key for i in report.items if i.depth == 1}


def test_string_literals_naming_the_target_are_unknown_sites() -> None:
    report = analyze(TOKENIZE)
    literals = [u for u in report.unknown if u.reason == "string_literal"]
    assert [(u.site.path, u.site.start_line, u.callee_text) for u in literals] == [
        ("pkg/core.py", 19, '"_tokenize"')
    ]
    assert literals[0].site.symbol_key == "file:pkg/core.py"  # module-level code
    parse_literals = [u for u in analyze(PARSE).unknown if u.reason == "string_literal"]
    assert [(u.site.path, u.site.start_line) for u in parse_literals] == [("pkg/__init__.py", 3)]
    assert report.stats.unknown == len(report.unknown)


def test_unknown_sites_are_never_in_the_ranked_items_and_never_dropped() -> None:
    report = analyze(TOKENIZE)
    ranked = {item.symbol_key for item in report.items}
    assert report.unknown, "the unknown bucket must not be empty here"
    assert all(u.site.symbol_key not in {TOKENIZE} for u in report.unknown)
    assert ranked  # the items are reported alongside, not instead


def test_sites_inside_the_target_itself_are_left_out() -> None:
    files = {
        "a.py": (
            "def dispatch(obj):\n"
            "    obj.dispatch()\n"
            '    return getattr(obj, "dispatch")\n'
            "\n\n"
            "def other(obj):\n"
            "    obj.dispatch()\n"
        )
    }
    source = MemoryImpactData(graph_of(files), files)
    report = analyze("py:a.py:dispatch:function", source)
    assert [(u.site.start_line, u.reason) for u in report.unknown] == [(7, "not_found")]


def test_literal_pattern_needs_the_whole_string() -> None:
    pattern = literal_pattern("parse")
    assert pattern.search('x = "parse"')
    assert pattern.search("x = 'parse'")
    assert pattern.search("x = `parse`")
    assert not pattern.search('x = "parser"')
    assert not pattern.search("x = \"parse'")
    assert not pattern.search("parse(x)")


def test_files_and_short_names_skip_the_literal_search() -> None:
    files = {"b.py": 'def f():\n    return "f"\n\n\ndef g():\n    return "f"\n'}
    source = MemoryImpactData(graph_of(files), files)
    assert analyze("py:b.py:f:function", source).unknown == []
    graph = CodeGraph()
    file_node = Node(
        key="file:x.py", kind=NodeKind.FILE, name="x.py", qualified_name="x.py", path="x.py"
    )
    empty = MemoryImpactData(graph, {})
    assert unknown_sites(empty, 1, ["file:x.py"], {"file:x.py": file_node}) == []


def test_the_bucket_is_capped_and_sorted() -> None:
    body = "".join(f"def c{i}(o):\n    o.target()\n\n\n" for i in range(12))
    files = {"c.py": "def target():\n    pass\n\n\n" + body}
    source = MemoryImpactData(graph_of(files), files)
    graph = source.code_graph
    sites = unknown_sites(
        source,
        1,
        ["py:c.py:target:function"],
        {"py:c.py:target:function": graph.nodes["py:c.py:target:function"]},
        limit=5,
    )
    assert len(sites) == 5
    lines = [s.site.start_line for s in sites]
    assert lines == sorted(lines)
