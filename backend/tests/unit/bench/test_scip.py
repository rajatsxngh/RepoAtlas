from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from repoatlas.bench._proto import scip_pb2
from repoatlas.bench.scip import Occurrence, Range, ScipError, ScipIndex, path_prefix
from tests.unit.bench.fixture_indexes import FIXTURES, PY, TS, minipy_bytes, mints_bytes

PROTO = Path(scip_pb2.__file__).with_name("scip.proto")
PROTO_SHA256 = "b38021b65ef90cbbf6af9c829ff75192859ad9b5da05439ef154bea4ceb2bf03"


def minipy() -> ScipIndex:
    return ScipIndex.load(FIXTURES / "minipy" / "index.scip")


def mints() -> ScipIndex:
    return ScipIndex.load(FIXTURES / "mints" / "index.scip")


def test_proto_is_the_pinned_file() -> None:
    assert hashlib.sha256(PROTO.read_bytes()).hexdigest() == PROTO_SHA256


def test_bindings_were_generated_from_the_proto() -> None:
    text = PROTO.read_text(encoding="utf-8")
    top_level = set(re.findall(r"^message (\w+)", text, flags=re.MULTILINE))
    assert scip_pb2.DESCRIPTOR.name == "scip.proto"
    assert set(scip_pb2.DESCRIPTOR.message_types_by_name) == top_level
    fields = {f.name for f in scip_pb2.Occurrence.DESCRIPTOR.fields}
    assert {"range", "symbol", "symbol_roles", "enclosing_range"} <= fields


def test_committed_fixtures_match_their_specs() -> None:
    assert (FIXTURES / "minipy" / "index.scip").read_bytes() == minipy_bytes()
    assert (FIXTURES / "mints" / "index.scip").read_bytes() == mints_bytes()


def test_metadata_and_documents() -> None:
    index = minipy()
    assert (index.tool_name, index.tool_version) == ("scip-python", "0.6.8")
    assert [d.path for d in index.documents()] == [
        "src/mini/core.py",
        "src/mini/util.py",
        "tests/test_core.py",
    ]


def test_subproject_paths_get_the_prefix() -> None:
    index = mints()
    assert index.prefix == "packages/lib/"
    assert [d.path for d in index.documents()] == [
        "packages/lib/src/math.ts",
        "packages/lib/test/math.test.ts",
    ]
    add = index.definition(TS + "src/`math.ts`/add().")
    assert add is not None
    assert (add.path, add.line) == ("packages/lib/src/math.ts", 1)


@pytest.mark.parametrize(
    ("root", "prefix"),
    [
        ("file:///src", ""),
        ("file:///src/", ""),
        ("file:///src/packages/remeda", "packages/remeda/"),
        ("file:///tmp/work/x", "x/"),
        ("file:///elsewhere", ""),
    ],
)
def test_path_prefix(root: str, prefix: str) -> None:
    assert path_prefix(root) == prefix


def test_definitions_are_sorted_and_non_local() -> None:
    defs = minipy().definitions()
    assert defs == sorted(defs)
    assert all(d.is_definition and not d.is_local for d in defs)
    names = [d.symbol.removeprefix(PY) for d in defs if d.path == "src/mini/util.py"]
    assert names == ["`mini.util`/helper().", "`mini.util`/unused().", "`mini.util`/twice()."]


def test_definition_location_is_the_name() -> None:
    run = minipy().definition(PY + "`mini.core`/Child#run().")
    assert run is not None
    assert run.range == Range(12, 8, 12, 11)
    assert run.enclosing_range == Range(12, 0, 13, 28)


def test_references_exclude_definitions() -> None:
    refs = minipy().references(PY + "`mini.util`/helper().")
    assert [(r.path, r.line) for r in refs] == [
        ("src/mini/core.py", 1),
        ("src/mini/core.py", 6),
        ("src/mini/core.py", 9),
        ("src/mini/core.py", 23),
        ("src/mini/util.py", 10),
        ("src/mini/util.py", 10),
    ]
    assert not any(r.is_definition for r in refs)
    assert minipy().references(PY + "nothing().") == []


def test_enclosing_definition_is_innermost() -> None:
    index = minipy()
    by_line = {r.line: r for r in index.references(PY + "`mini.util`/helper().")}
    # line 6 is inside Base.__init__, which is inside Base.
    assert index.enclosing_definition(by_line[6]).symbol == PY + "`mini.core`/Base#__init__()."  # type: ignore[union-attr]
    outer = [o.symbol.removeprefix(PY) for o in index.enclosing_definitions(by_line[6])]
    assert outer == ["`mini.core`/Base#__init__().", "`mini.core`/Base#"]
    # Module-level imports have no enclosing definition.
    assert index.enclosing_definition(by_line[1]) is None


def test_enclosing_definition_never_returns_the_occurrence_itself() -> None:
    index = minipy()
    entry = index.definition(PY + "`mini.core`/entry().")
    assert entry is not None
    assert index.enclosing_definition(entry) is None


def test_typescript_test_callbacks_have_no_enclosing_definition() -> None:
    index = mints()
    refs = index.references(TS + "src/`math.ts`/add().")
    in_test = [r for r in refs if r.path.endswith("math.test.ts")]
    assert [r.line for r in in_test] == [2, 6]
    assert all(index.enclosing_definition(r) is None for r in in_test)
    in_sum = next(r for r in refs if r.line == 6 and r.path.endswith("math.ts"))
    assert index.enclosing_definition(in_sum).symbol == TS + "src/`math.ts`/sum()."  # type: ignore[union-attr]


def test_implementations() -> None:
    index = minipy()
    assert index.implementations(PY + "`mini.core`/Base#run().") == [
        PY + "`mini.core`/Child#run()."
    ]
    assert index.implementations(PY + "`mini.core`/make().") == []


def test_roles() -> None:
    occ = Occurrence("a.py", Range(0, 0, 0, 1), "local 3", 0x2)
    assert occ.is_import and occ.is_local and not occ.is_definition
    assert occ.line == 1


def test_range_contains() -> None:
    outer = Range(1, 0, 5, 10)
    assert outer.contains(Range(1, 0, 1, 3))
    assert outer.contains(Range(5, 2, 5, 10))
    assert not outer.contains(Range(5, 2, 5, 11))
    assert not outer.contains(Range(0, 9, 1, 2))
    assert Range.from_proto([1, 2]) is None
    assert Range.from_proto([1, 2, 3]) == Range(1, 2, 1, 3)


def test_garbage_is_rejected() -> None:
    with pytest.raises(ScipError):
        ScipIndex.from_bytes(b"\xff\xff\xff")


def test_reads_a_real_index_when_present() -> None:
    """The local benchmark index (gitignored) parses and has the expected shape."""
    root = Path(__file__).resolve().parents[4] / "eval" / "artifacts" / "click"
    found = sorted(root.glob("*/index.scip"))
    if not found:
        pytest.skip("click's index.scip is local only")
    index = ScipIndex.load(found[0])
    assert index.tool_version == "0.6.8"
    assert len(index.documents()) == 91
    core = PY.replace("mini 0.1.0", "click 8.5.1")
    invoke = index.definition(core + "`click.core`/Context#invoke().")
    assert invoke is not None and invoke.path == "src/click/core.py"
