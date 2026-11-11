from __future__ import annotations

from repoatlas.sandbox.recipe import parse_recipe
from repoatlas.sandbox.runner import selection_args, suite_script
from tests.integration.sandbox.fixtures import MINIPY_RECIPE, minipy_recipe


def test_script_runs_in_src_with_selected_node_ids() -> None:
    script = suite_script(minipy_recipe(), ["tests/t.py::test_b[x y]", "tests/t.py::test_a"])
    assert script.startswith("cd /src\n")
    assert script.endswith(
        "--junitxml=/tmp/out/report tests/t.py::test_a 'tests/t.py::test_b[x y]'"
    )


def test_files_selection_dedupes_files() -> None:
    recipe = parse_recipe(MINIPY_RECIPE.replace("reporter: junit", "reporter: vitest-json"))
    only = ["src/a.test.ts > x > y", "src/a.test.ts > z", "src/b.test.ts > q"]
    assert selection_args(recipe, only) == ["src/a.test.ts", "src/b.test.ts"]


def test_tmp_workdir_copies_source_and_links_node_modules() -> None:
    recipe = parse_recipe(MINIPY_RECIPE + "workdir: tmp\nenv: {TZ: UTC}\n")
    script = suite_script(recipe)
    assert "tar --exclude=./node_modules" in script
    assert "ln -s /src/node_modules /tmp/work/node_modules" in script
    assert "\ncd /tmp/work\nexport TZ=UTC\npython -m pytest" in script
