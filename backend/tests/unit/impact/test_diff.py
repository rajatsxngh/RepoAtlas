"""Diff targets (T4): unified-diff parsing and mapping changed lines to symbols."""

from __future__ import annotations

from functools import cache

import pytest

from repoatlas.impact.data import MemoryImpactData
from repoatlas.impact.diff import changed_symbols, changed_symbols_in, parse_unified_diff
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.schema import DiffTarget
from repoatlas.store.errors import InvalidQuery
from tests.unit.impact.helpers import (
    BASE_RUN,
    CHILD_RUN,
    MAIN,
    PARSE,
    TEST_PARSE,
    TOKENIZE,
    project_data,
)

CHILD = "py:pkg/core.py:Child:class"
CORE_FILE = "file:pkg/core.py"


@cache
def data() -> MemoryImpactData:
    return project_data()


def changed(diff: str, side: str = "old") -> list[str]:
    return changed_symbols_in(data(), 1, diff, side=side)  # type: ignore[arg-type]


def core(hunks: str) -> str:
    return "diff --git a/pkg/core.py b/pkg/core.py\n--- a/pkg/core.py\n+++ b/pkg/core.py\n" + hunks


def test_an_edited_line_maps_to_its_innermost_symbol() -> None:
    diff = core(
        "@@ -1,2 +1,2 @@\n def _tokenize(text):\n"
        "-    return text.split()\n+    return text.split(' ')\n"
    )
    assert changed(diff) == [TOKENIZE]
    assert changed(diff, "new") == [TOKENIZE]


def test_an_addition_maps_to_the_symbol_around_it() -> None:
    diff = core(
        "@@ -5,2 +5,3 @@\n def parse(text):\n"
        "+    text = text.strip()\n     return _tokenize(text)\n"
    )
    assert changed(diff) == [PARSE]


def test_a_deletion_only_hunk_maps_to_the_enclosing_symbol() -> None:
    diff = core("@@ -10,2 +10,1 @@\n     def run(self):\n-        return 1\n")
    assert changed(diff) == [BASE_RUN]  # the removed line itself
    assert changed(diff, "new") == [BASE_RUN]  # where it was, in the new numbering


def test_module_level_additions_map_to_the_file_and_decorators_to_their_symbol() -> None:
    at_end = core('@@ -19 +19,2 @@\n HANDLERS = {"tok": "_tokenize"}\n+EXTRA = 1\n')
    assert changed(at_end) == [CORE_FILE]
    decorator = core("@@ -13,2 +13,3 @@\n \n+@register\n class Child(Base):\n")
    assert changed(decorator) == [CHILD]


def test_hunks_after_an_insertion_use_old_numbers_on_the_old_side() -> None:
    # three lines added at the top shift everything below by 3 on the new side
    diff = core(
        "@@ -1,1 +1,4 @@\n+import os\n+\n+\n def _tokenize(text):\n"
        "@@ -15,2 +18,2 @@\n     def run(self):\n-        return 2\n+        return 3\n"
    )
    (change,) = parse_unified_diff(diff)
    assert change.old_changed == {16}
    assert change.new_changed == {1, 2, 3, 19}
    assert changed(diff) == [CHILD_RUN, TOKENIZE]


def test_new_deleted_binary_and_unknown_files_contribute_nothing() -> None:
    diff = (
        "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n+def f():\n+    pass\n"
        "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
        "diff --git a/elsewhere.py b/elsewhere.py\n--- a/elsewhere.py\n+++ b/elsewhere.py\n"
        "@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        "diff --git a/pkg/cli.py b/pkg/cli.py\n--- a/pkg/cli.py\n+++ b/pkg/cli.py\n"
        "@@ -7 +7 @@\n-    return parse(argv)\n+    return parse(argv or [])\n"
        "\\ No newline at end of file\n"
    )
    changes = parse_unified_diff(diff)
    assert [(c.old_path, c.new_path, c.binary) for c in changes] == [
        (None, "new.py", False),
        (None, None, True),
        ("elsewhere.py", "elsewhere.py", False),
        ("pkg/cli.py", "pkg/cli.py", False),
    ]
    assert changed(diff) == [MAIN]


def test_plain_diff_u_output_with_timestamps_and_several_files() -> None:
    diff = (
        "--- pkg/core.py\t2026-01-01 10:00:00\n+++ pkg/core.py\t2026-01-02 10:00:00\n"
        "@@ -2 +2 @@\n-    return text.split()\n+    return text.split(',')\n"
        "--- pkg/cli.py\t2026-01-01\n+++ pkg/cli.py\t2026-01-02\n"
        "@@ -5,1 +5,1 @@\n-    obj = load()\n+    obj = make()\n"
    )
    assert changed(diff) == [MAIN, TOKENIZE]


def test_garbage_gives_no_symbols() -> None:
    assert changed("hello\nworld\n") == []
    assert changed(core("@@ -900 +900 @@\n-x\n+y\n")) == []  # past the end of the file


def test_a_diff_target_combines_the_impact_of_every_changed_symbol() -> None:
    diff = core(
        "@@ -2 +2 @@\n-    return text.split()\n+    return text.split(' ')\n"
        "@@ -11 +11 @@\n-        return 1\n+        return 0\n"
    )
    report = ImpactEngine(data()).analyze(1, DiffTarget(diff=diff))
    assert report.targets == [BASE_RUN, TOKENIZE]
    keys = [item.symbol_key for item in report.items]
    assert {PARSE, MAIN, TEST_PARSE, CHILD_RUN} <= set(keys)
    assert TOKENIZE not in keys and BASE_RUN not in keys
    assert report.target.kind == "diff"


def test_too_many_changed_symbols_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import repoatlas.impact.engine as engine

    monkeypatch.setattr(engine, "MAX_TARGETS", 1)
    diff = core(
        "@@ -2 +2 @@\n-    return text.split()\n+    return 1\n"
        "@@ -11 +11 @@\n-        return 1\n+        return 0\n"
    )
    with pytest.raises(InvalidQuery):
        ImpactEngine(data()).analyze(1, DiffTarget(diff=diff))


def test_changed_symbols_takes_any_locator() -> None:
    diff = core("@@ -2 +2 @@\n-    return text.split()\n+    return 1\n")
    assert changed_symbols(1, diff, locator=data()) == [TOKENIZE]
