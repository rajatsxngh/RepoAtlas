"""Test links (GRA-7): ``tested_by`` edges from production code to the tests that exercise it.

1. Imports: a test file importing a production file links that file to the test file
   (``import_resolver``), as does importing a symbol through it (``from pkg import Cart`` also
   links the file that defines ``Cart``). The site is the import statement.
2. Calls: a test that calls a production symbol (directly or from a function nested in it) links
   the symbol to the test node (``name_match``), with the call edge's confidence and site.
3. Naming conventions: ``test_foo.py``/``foo_test.py`` ↔ ``foo.py`` and ``foo.test.ts``/
   ``foo.spec.ts``/``__tests__/foo.ts`` ↔ ``foo.ts`` (``convention``). A candidate in the test's
   own folder (or the folder above ``__tests__``) wins; otherwise the candidate whose folders
   best match the test's (ignoring ``src``, ``test``, ``tests``, ``__tests__``); ties link nothing.

Production means a parsed file that is not a test file. Sites of ``tested_by`` edges lie in the
test file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from repoatlas.graph.bindings import ResolvedStmt
from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.index import family
from repoatlas.graph.keys import file_key
from repoatlas.graph.model import Edge, EdgeKind, Node, NodeKind, Provenance
from repoatlas.graph.tiers import TESTED_BY_CONVENTION_CONFIDENCE, TESTED_BY_IMPORT_CONFIDENCE
from repoatlas.ingest.inventory import Inventory

_IGNORED_FOLDERS = frozenset({"src", "test", "tests", "__tests__"})


def add_tested_by_edges(
    inventory: Inventory,
    resolved: Iterable[tuple[str, list[ResolvedStmt]]],
    call_edges: Iterable[Edge],
    nodes: Mapping[str, Node],
    edges: EdgeSet,
) -> None:
    tests = {entry.path for entry in inventory.indexed if entry.is_test}
    production = {entry.path for entry in inventory.indexed if not entry.is_test}

    for path, statements in resolved:
        if path not in tests:
            continue
        for stmt, result in statements:
            imported = list(result.files)
            imported += [target.path for _local, target in result.bindings if target.path]
            for module in dict.fromkeys(imported):
                if module in production:
                    edges.add(
                        file_key(module),
                        file_key(path),
                        EdgeKind.TESTED_BY,
                        Provenance.IMPORT_RESOLVER,
                        TESTED_BY_IMPORT_CONFIDENCE,
                        site=stmt.range,
                    )

    for call in call_edges:
        test = _enclosing_test(call.src, nodes)
        callee = nodes.get(call.dst)
        if test is None or callee is None or callee.path not in production:
            continue
        edges.add(
            call.dst,
            test,
            EdgeKind.TESTED_BY,
            Provenance.NAME_MATCH,
            call.confidence,
            site=call.site,
        )

    by_stem = _production_stems(inventory, production)
    for path in sorted(tests):
        named = convention_target(path, by_stem)
        if named is not None:
            edges.add(
                file_key(named),
                file_key(path),
                EdgeKind.TESTED_BY,
                Provenance.CONVENTION,
                TESTED_BY_CONVENTION_CONFIDENCE,
            )


def _enclosing_test(key: str, nodes: Mapping[str, Node]) -> str | None:
    """The test node that is or encloses ``key`` (``None`` at file level)."""
    node = nodes.get(key)
    while node is not None and node.kind not in (NodeKind.FILE, NodeKind.DIR):
        if node.kind == NodeKind.TEST:
            return node.key
        node = nodes.get(node.parent_key) if node.parent_key is not None else None
    return None


def _split(path: str) -> tuple[list[str], str]:
    *folders, name = path.split("/")
    return folders, name


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _production_stems(
    inventory: Inventory, production: set[str]
) -> dict[tuple[str, str], list[str]]:
    stems: dict[tuple[str, str], list[str]] = {}
    for entry in inventory.indexed:
        if entry.path in production:
            stem = _stem(_split(entry.path)[1])
            stems.setdefault((family(entry.language), stem), []).append(entry.path)
    return stems


def tested_stem(path: str) -> str | None:
    """The base name of the production file a test file is named after, if any."""
    folders, name = _split(path)
    if name.endswith(".py"):
        if name.startswith("test_") and len(name) > len("test_.py"):
            return name[len("test_") : -len(".py")]
        if name.endswith("_test.py") and len(name) > len("_test.py"):
            return name[: -len("_test.py")]
        return None
    stem = _stem(name)
    for marker in (".test", ".spec"):
        if stem.endswith(marker) and len(stem) > len(marker):
            return stem[: -len(marker)]
    return stem if "__tests__" in folders else None


def convention_target(
    test_path: str, by_stem: Mapping[tuple[str, str], Sequence[str]]
) -> str | None:
    """The production file ``test_path`` is named after, or ``None`` if there is no single best."""
    stem = tested_stem(test_path)
    if stem is None:
        return None
    language = "py" if test_path.endswith(".py") else "js"
    candidates = sorted(by_stem.get((language, stem), ()))
    if not candidates:
        return None
    folders, _name = _split(test_path)
    home = folders[: folders.index("__tests__")] if "__tests__" in folders else folders
    for candidate in candidates:
        if _split(candidate)[0] in (folders, home):
            return candidate
    if len(candidates) == 1:
        return candidates[0]
    wanted = Counter(f for f in folders if f not in _IGNORED_FOLDERS)
    scores = {
        candidate: sum(
            (
                wanted & Counter(f for f in _split(candidate)[0] if f not in _IGNORED_FOLDERS)
            ).values()
        )
        for candidate in candidates
    }
    best = max(scores.values())
    winners = [candidate for candidate, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 and best > 0 else None
