from __future__ import annotations

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
