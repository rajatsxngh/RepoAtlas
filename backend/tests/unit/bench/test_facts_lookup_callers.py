from __future__ import annotations

from pathlib import Path

from repoatlas.bench.facts import Facts, import_lines
from repoatlas.bench.generate import display_names, gen_callers, gen_lookup, stratified_order
from repoatlas.bench.schema import AnswerSetTruth, LookupTruth
from repoatlas.bench.validate import validate
from repoatlas.canonical import canonical_json
from tests.unit.bench.conftest import MINIPY_COMMIT, fixture_facts

CORE = "py:src/mini/core.py:"
UTIL = "py:src/mini/util.py:"
TESTS = "py:tests/test_core.py:"
MATH = "ts:packages/lib/src/math.ts:"
MATH_TEST = "ts:packages/lib/test/math.test.ts:"


def callers(facts: Facts, key: str) -> list[str]:
    return sorted(facts.callers.get(key, ()))


def test_definitions_map_to_symbol_keys_by_name_line(minipy: Facts) -> None:
    run = minipy.definitions[CORE + "Child.run:method"]
    assert (run.path, run.line, run.scip_symbol.endswith("Child#run().")) == (
        "src/mini/core.py",
        13,
        True,
    )
    assert minipy.unmatched_definitions == 0
    assert minipy.definitions[TESTS + "test_entry:test"].is_test_file


def test_callers_are_innermost_enclosing_definitions(minipy: Facts) -> None:
    assert callers(minipy, UTIL + "helper:function") == [
        CORE + "Base.__init__:method",
        CORE + "Base.run:method",
        CORE + "entry:function",
        UTIL + "twice:function",
    ]
    assert callers(minipy, CORE + "make:function") == [
        CORE + "entry:function",
        TESTS + "test_make:test",
    ]


def test_call_sites_record_the_reference_lines(minipy: Facts) -> None:
    helper = UTIL + "helper:function"
    assert minipy.call_sites[(helper, UTIL + "twice:function")] == [
        ("src/mini/util.py", 10),
        ("src/mini/util.py", 10),
    ]
    assert minipy.call_sites[(CORE + "Base.__init__:method", CORE + "make:function")] == [
        ("src/mini/core.py", 18)
    ]


def test_import_references_are_not_calls(minipy: Facts) -> None:
    # twice is imported by core.py and the tests on line 1/2, called by Child.run and test_twice.
    assert callers(minipy, UTIL + "twice:function") == [
        CORE + "Child.run:method",
        TESTS + "test_twice:test",
    ]
    # nested() imports unused inside its body, then calls it once.
    assert callers(minipy, UTIL + "unused:function") == [CORE + "nested:function"]


def test_constructor_calls_count_for_the_class_and_its_constructor(minipy: Facts) -> None:
    # make() calls Base(1); Child subclasses Base (a reference, not a call).
    assert callers(minipy, CORE + "Base:class") == [CORE + "Child:class", CORE + "make:function"]
    assert callers(minipy, CORE + "Base.__init__:method") == [CORE + "make:function"]


def test_overrides_are_kept_apart(minipy: Facts) -> None:
    assert callers(minipy, CORE + "Base.run:method") == [CORE + "entry:function"]
    assert minipy.overrides == {CORE + "Base.run:method": [CORE + "Child.run:method"]}
    assert sorted(minipy.override_callers[CORE + "Base.run:method"]) == [TESTS + "test_child:test"]


def test_file_references_include_imports(minipy: Facts) -> None:
    assert sorted(minipy.file_references["tests/test_core.py"]) == [
        CORE + "Child.run:method",
        CORE + "Child:class",
        CORE + "entry:function",
        CORE + "make:function",
        UTIL + "twice:function",
    ]


def test_references_without_a_scip_scope_use_the_parsed_span(mints: Facts) -> None:
    assert callers(mints, MATH + "add:function") == [
        MATH + "Counter.bump:method",
        MATH + "sum:function",
        MATH + "triple:function",
        MATH_TEST + "math > adds:test",
    ]
    assert callers(mints, MATH + "sum:function") == [MATH_TEST + "math > sums:test"]
    loc = mints.location(MATH_TEST + "math > adds:test")
    assert (loc.path, loc.line) == ("packages/lib/test/math.test.ts", 5)


def test_import_lines() -> None:
    py = "import os\nfrom a import (\n    b,\n    c,\n)\n\ndef f():\n    from x import y\n"
    assert import_lines("m.py", py) == {1, 2, 3, 4, 5, 8}
    ts = (
        'import { a } from "./a";\n'
        "import {\n  b,\n  c,\n} from './b';\n"
        'import "./side-effect";\n'
        'export { d } from "./d";\n'
        "export const e = 1;\n"
        "export * from './f';\n"
    )
    assert import_lines("m.ts", ts) == {1, 2, 3, 4, 5, 6, 7, 9}
    assert import_lines("bad.py", "def (") == set()


def test_display_names(minipy: Facts) -> None:
    names = display_names(minipy)
    assert names[UTIL + "helper:function"] == "helper"
    assert names[CORE + "Base.run:method"] == "Base.run"
    assert names[CORE + "Child.run:method"] == "Child.run"


def test_stratified_order_is_seeded_and_spread() -> None:
    import random

    items = list(range(40))
    one = stratified_order(items, lambda i: i, str, random.Random(1))
    assert one == stratified_order(items, lambda i: i, str, random.Random(1))
    assert one != stratified_order(items, lambda i: i, str, random.Random(2))
    assert sorted(i // 10 for i in one[:4]) == [0, 1, 2, 3]


def test_gen_lookup(minipy: Facts) -> None:
    questions = gen_lookup(minipy, seed=1)
    assert [q.id for q in questions] == [f"minipy-lookup-{n:03d}" for n in range(1, 7)]
    assert [canonical_json(q) for q in questions] == [
        canonical_json(q) for q in gen_lookup(minipy, seed=1)
    ]
    for q in questions:
        assert isinstance(q.ground_truth, LookupTruth)
        definition = minipy.definitions[q.ground_truth.definition.symbol_key]
        assert q.ground_truth.definition.line == definition.line
        assert f"`{display_names(minipy)[definition.key]}`" in q.text
        assert not definition.is_test_file and definition.name != "__init__"
        assert q.provenance == "scip" and q.commit == MINIPY_COMMIT


def test_gen_callers(minipy: Facts) -> None:
    questions = gen_callers(minipy, seed=1)
    targets = sorted(q.text.split("`")[1] for q in questions)
    assert targets == ["helper", "make", "twice"]  # the only functions with 2 to 15 callers
    for q in questions:
        assert isinstance(q.ground_truth, AnswerSetTruth)
        assert 2 <= len(q.ground_truth.answers) <= 15


def test_override_notes() -> None:
    from repoatlas.bench.generate import _override_note

    facts, _ = fixture_facts("minipy", "minipy", MINIPY_COMMIT)
    note = _override_note(facts, CORE + "Base.run:method")
    assert note == (
        f"Overridden by {CORE}Child.run:method. "
        f"Callers of the overrides (not in the answer): {TESTS}test_child:test."
    )
    assert _override_note(facts, UTIL + "helper:function") == ""


def test_generated_questions_validate() -> None:
    facts, files = fixture_facts("minipy", "minipy", MINIPY_COMMIT)
    questions = gen_callers(facts, seed=3) + gen_lookup(facts, seed=3)
    assert validate(questions, {"minipy": files}) == []


def test_library_paths() -> None:
    from repoatlas.bench.generate import _is_library

    assert _is_library("src/click/core.py")
    assert _is_library("packages/remeda/src/pipe.ts")
    assert not _is_library("examples/aliases/aliases.py")
    assert not _is_library("packages/docs/scripts/x.ts")


def test_names_target_accepts_names_constructors_and_aliases(minipy: Facts) -> None:
    from repoatlas.bench.facts import _names_target

    helper = minipy.definitions[UTIL + "helper:function"]
    init = minipy.definitions[CORE + "Base.__init__:method"]
    assert _names_target("helper", helper, "", minipy)
    assert _names_target("Base", init, "", minipy)  # new Base( → constructor
    assert _names_target("h", helper, "from mini.util import helper as h\n", minipy)
    assert not _names_target("Response", helper, "", minipy)


def test_misresolved_references_are_ignored_and_taint_their_names(tmp_path: Path) -> None:
    import shutil

    from repoatlas.bench.facts import build_facts
    from repoatlas.bench.generate import _trusted
    from repoatlas.bench.repos import RepoFiles
    from repoatlas.bench.scip import ScipIndex
    from repoatlas.ingest.source import DirectorySourceTree
    from repoatlas.sandbox.parsed import parse_tree
    from tests.unit.bench.fixture_indexes import FIXTURES, MINIPY, PY, build, r

    root = tmp_path / "minipy"
    shutil.copytree(FIXTURES / "minipy", root)
    test_file = root / "tests" / "test_core.py"
    test_file.write_text(
        test_file.read_text(encoding="utf-8") + "\n\ndef test_wrong():\n    Child(1)\n",
        encoding="utf-8",
    )
    specs = {k: list(v) for k, v in MINIPY.items()}
    # The indexer resolves the text `Child` on line 22 to Base (like httpx's off-by-one).
    specs["tests/test_core.py"] += [r(22, "Child", PY + "`mini.core`/Base#")]
    data = build(root, specs, tool="scip-python", version="0.6.8", project_root="file:///src")
    files = RepoFiles("minipy", "1" * 40, DirectorySourceTree(root))
    facts = build_facts(ScipIndex.from_bytes(data), parse_tree(files.tree), files)
    assert facts.misresolved == {("Child", CORE + "Base:class"): 1}
    assert "py:tests/test_core.py:test_wrong:test" not in facts.callers[CORE + "Base:class"]
    # Both sides are tainted: the text written (Child) and the target SCIP chose (Base).
    assert facts.tainted_names() == {"Child", "Base"}
    assert not _trusted(facts, facts.definitions[CORE + "Child.run:method"])
    assert not _trusted(facts, facts.definitions[CORE + "Base.run:method"])
    assert _trusted(facts, facts.definitions[UTIL + "helper:function"])


def test_names_target_accepts_import_spans_quotes_and_default_imports(minipy: Facts) -> None:
    from repoatlas.bench.facts import _names_target

    helper = minipy.definitions[UTIL + "helper:function"]
    assert _names_target("helper as h", helper, "", minipy)
    assert _names_target('"helper"', helper, "", minipy)
    assert _names_target("defaultHelper", helper, 'import defaultHelper from "./util";\n', minipy)
    assert not _names_target("other", helper, 'import defaultHelper from "./util";\n', minipy)


def test_property_setters_are_callers_scip_has_no_definition_for(tmp_path: Path) -> None:
    import shutil

    from repoatlas.bench.facts import build_facts
    from repoatlas.bench.repos import RepoFiles
    from repoatlas.bench.scip import ScipIndex
    from repoatlas.ingest.source import DirectorySourceTree
    from repoatlas.sandbox.parsed import parse_tree
    from tests.unit.bench.fixture_indexes import FIXTURES, MINIPY, PY, build, r

    root = tmp_path / "minipy"
    shutil.copytree(FIXTURES / "minipy", root)
    util = root / "src" / "mini" / "util.py"
    box = [
        "",
        "",
        "class Box:",
        "    @property",
        "    def size(self):",
        "        return 1",
        "",
        "    @size.setter",
        "    def size(self, value):",
        "        helper(value)",
        "",
    ]
    util.write_text(util.read_text(encoding="utf-8") + "\n".join(box), encoding="utf-8")
    specs = {k: list(v) for k, v in MINIPY.items()}
    from tests.unit.bench.fixture_indexes import d

    specs["src/mini/util.py"] += [
        d(13, "Box", PY + "`mini.util`/Box#", (13, 20)),
        d(15, "size", PY + "`mini.util`/Box#size().", (15, 16)),
        r(19, "size", PY + "`mini.util`/Box#size()."),  # the setter's def, listed as a reference
        r(20, "helper", PY + "`mini.util`/helper()."),
    ]
    data = build(root, specs, tool="scip-python", version="0.6.8", project_root="file:///src")
    files = RepoFiles("minipy", "1" * 40, DirectorySourceTree(root))
    facts = build_facts(ScipIndex.from_bytes(data), parse_tree(files.tree), files)
    setter = UTIL + "Box.size:method~2"
    assert setter in facts.callers[UTIL + "helper:function"]
    assert UTIL + "Box:class" not in facts.callers[UTIL + "helper:function"]
    location = facts.location(setter)
    assert (location.path, location.line) == ("src/mini/util.py", 19)


def test_declares_type() -> None:
    from repoatlas.bench.facts import _declares_type

    text = "export interface $ZodBigInt<T> {}\nexport const $ZodBigInt = make();\ntype Box = 1;\n"
    assert _declares_type(text, "$ZodBigInt")
    assert _declares_type(text, "Box")
    assert not _declares_type(text, "$ZodBig")
    assert not _declares_type("const x = 1;\n", "x")
