"""The committed SCIP fixtures in ``tests/fixtures/bench/``, written as readable specs.

Each spec line is what scip-python 0.6.8 or scip-typescript 0.6.2 reports for one occurrence of
the fixture source, in the indexers' observed style: definitions have role 1, references role 8
(Python) or 0 (TypeScript), function and class definitions carry enclosing ranges, imports have
no import role, and overrides carry ``is_implementation`` relationships.

Regenerate the committed indexes with ``uv run python -m tests.unit.bench.fixture_indexes``;
``test_scip.py`` checks that the committed bytes match these specs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repoatlas.bench._proto import scip_pb2

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bench"
PY = "scip-python python mini 0.1.0 "
TS = "scip-typescript npm lib 1.0.0 "


@dataclass(frozen=True)
class Spec:
    line: int  # 1-based
    name: str  # text of the occurrence on that line
    symbol: str
    definition: bool = False
    enclosing: tuple[int, int] | None = None  # 1-based inclusive lines
    nth: int = 0  # which occurrence of ``name`` on the line
    implements: str | None = None  # base symbol, for overrides


def d(
    line: int, name: str, symbol: str, enclosing: tuple[int, int] | None = None, **kw: str
) -> Spec:
    return Spec(line, name, symbol, True, enclosing, implements=kw.get("implements"))


def r(line: int, name: str, symbol: str, nth: int = 0) -> Spec:
    return Spec(line, name, symbol, nth=nth)


MINIPY: dict[str, list[Spec]] = {
    "src/mini/util.py": [
        d(1, "helper", PY + "`mini.util`/helper().", (1, 2)),
        d(5, "unused", PY + "`mini.util`/unused().", (5, 6)),
        d(9, "twice", PY + "`mini.util`/twice().", (9, 10)),
        r(10, "helper", PY + "`mini.util`/helper()."),
        r(10, "helper", PY + "`mini.util`/helper().", nth=1),
    ],
    "src/mini/core.py": [
        r(1, "helper", PY + "`mini.util`/helper()."),
        r(1, "twice", PY + "`mini.util`/twice()."),
        d(4, "Base", PY + "`mini.core`/Base#", (4, 9)),
        d(5, "__init__", PY + "`mini.core`/Base#__init__().", (5, 6)),
        r(6, "helper", PY + "`mini.util`/helper()."),
        d(8, "run", PY + "`mini.core`/Base#run().", (8, 9)),
        r(9, "helper", PY + "`mini.util`/helper()."),
        d(12, "Child", PY + "`mini.core`/Child#", (12, 14)),
        r(12, "Base", PY + "`mini.core`/Base#"),
        d(
            13,
            "run",
            PY + "`mini.core`/Child#run().",
            (13, 14),
            implements=PY + "`mini.core`/Base#run().",
        ),
        r(14, "twice", PY + "`mini.util`/twice()."),
        d(17, "make", PY + "`mini.core`/make().", (17, 18)),
        r(18, "Base", PY + "`mini.core`/Base#"),
        d(21, "entry", PY + "`mini.core`/entry().", (21, 23)),
        r(22, "make", PY + "`mini.core`/make()."),
        r(23, "run", PY + "`mini.core`/Base#run()."),
        r(23, "helper", PY + "`mini.util`/helper()."),
        d(26, "nested", PY + "`mini.core`/nested().", (26, 29)),
        r(27, "unused", PY + "`mini.util`/unused()."),
        r(29, "unused", PY + "`mini.util`/unused()."),
    ],
    "tests/test_core.py": [
        r(1, "Child", PY + "`mini.core`/Child#"),
        r(1, "entry", PY + "`mini.core`/entry()."),
        r(1, "make", PY + "`mini.core`/make()."),
        r(2, "twice", PY + "`mini.util`/twice()."),
        d(5, "test_entry", PY + "`tests.test_core`/test_entry().", (5, 6)),
        r(6, "entry", PY + "`mini.core`/entry()."),
        d(9, "test_make", PY + "`tests.test_core`/test_make().", (9, 10)),
        r(10, "make", PY + "`mini.core`/make()."),
        d(13, "test_child", PY + "`tests.test_core`/test_child().", (13, 14)),
        r(14, "Child", PY + "`mini.core`/Child#"),
        r(14, "run", PY + "`mini.core`/Child#run()."),
        d(17, "test_twice", PY + "`tests.test_core`/test_twice().", (17, 18)),
        r(18, "twice", PY + "`mini.util`/twice()."),
    ],
}

MINITS: dict[str, list[Spec]] = {
    "src/math.ts": [
        d(1, "add", TS + "src/`math.ts`/add().", (1, 3)),
        d(5, "sum", TS + "src/`math.ts`/sum().", (5, 7)),
        r(6, "add", TS + "src/`math.ts`/add()."),
        d(9, "Counter", TS + "src/`math.ts`/Counter#", (9, 16)),
        d(10, "total", TS + "src/`math.ts`/Counter#total."),
        d(12, "bump", TS + "src/`math.ts`/Counter#bump().", (12, 15)),
        r(13, "total", TS + "src/`math.ts`/Counter#total."),
        r(13, "add", TS + "src/`math.ts`/add()."),
        r(13, "total", TS + "src/`math.ts`/Counter#total.", nth=1),
        r(14, "total", TS + "src/`math.ts`/Counter#total."),
        # An arrow-function const: scip-typescript gives it no enclosing range.
        d(18, "triple", TS + "src/`math.ts`/triple."),
        r(18, "add", TS + "src/`math.ts`/add()."),
        r(18, "add", TS + "src/`math.ts`/add().", nth=1),
    ],
    "test/math.test.ts": [
        r(2, "add", TS + "src/`math.ts`/add()."),
        r(2, "sum", TS + "src/`math.ts`/sum()."),
        r(6, "add", TS + "src/`math.ts`/add()."),
        r(10, "sum", TS + "src/`math.ts`/sum()."),
    ],
}


def _column(text: str, name: str, nth: int) -> int:
    start = -1
    for _ in range(nth + 1):
        start = text.index(name, start + 1)
        # Skip matches inside a longer identifier.
        while (start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_")) or (
            start + len(name) < len(text)
            and (text[start + len(name)].isalnum() or text[start + len(name)] == "_")
        ):
            start = text.index(name, start + 1)
    return start


def build(
    root: Path, specs: dict[str, list[Spec]], *, tool: str, version: str, project_root: str
) -> bytes:
    """The index the specs describe, checked against the source files under ``root``."""
    index = scip_pb2.Index()
    index.metadata.tool_info.name = tool
    index.metadata.tool_info.version = version
    index.metadata.project_root = project_root
    language = "python" if tool == "scip-python" else "TypeScript"
    for path, items in specs.items():
        lines = (root / path).read_text(encoding="utf-8").splitlines()
        document = index.documents.add(relative_path=path, language=language)
        for spec in items:
            column = _column(lines[spec.line - 1], spec.name, spec.nth)
            occ = document.occurrences.add(
                range=[spec.line - 1, column, column + len(spec.name)],
                symbol=spec.symbol,
                symbol_roles=1 if spec.definition else (8 if tool == "scip-python" else 0),
            )
            if spec.enclosing is not None:
                first, last = spec.enclosing
                occ.enclosing_range.extend([first - 1, 0, last - 1, len(lines[last - 1])])
            if spec.definition:
                info = document.symbols.add(symbol=spec.symbol)
                if spec.implements:
                    info.relationships.add(symbol=spec.implements, is_implementation=True)
    return index.SerializeToString(deterministic=True)


def minipy_bytes() -> bytes:
    return build(
        FIXTURES / "minipy",
        MINIPY,
        tool="scip-python",
        version="0.6.8",
        project_root="file:///src",
    )


def mints_bytes() -> bytes:
    return build(
        FIXTURES / "mints" / "packages" / "lib",
        MINITS,
        tool="scip-typescript",
        version="0.6.2",
        project_root="file:///src/packages/lib",
    )


if __name__ == "__main__":
    (FIXTURES / "minipy" / "index.scip").write_bytes(minipy_bytes())
    (FIXTURES / "mints" / "index.scip").write_bytes(mints_bytes())
    print("Wrote the bench SCIP fixtures.")
