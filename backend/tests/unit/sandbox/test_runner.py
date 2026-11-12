from __future__ import annotations

import pytest

from repoatlas.sandbox.recipe import parse_recipe
from repoatlas.sandbox.runner import SandboxRunner, selection_args, suite_script
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


SCIP_TMP = """\
  indexer: scip-python
  workdir: tmp
  cwd: pkg
  setup: ["echo {} > cfg.json"]
"""


def test_scip_script_can_index_a_writable_copy_after_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[str] = []

    class Done(Exception):
        pass

    def fake_run(image: str, script: str, **kwargs: object) -> object:
        scripts.append(script)
        raise Done

    recipe = parse_recipe(MINIPY_RECIPE.replace("  indexer: scip-python\n", SCIP_TMP))
    monkeypatch.setattr("repoatlas.sandbox.runner.run_hardened", fake_run)
    with pytest.raises(Done):
        SandboxRunner(recipe, "img").run_scip()
    script = scripts[0]
    assert "tar -C /tmp/work -xf -" in script
    assert "\ncd /tmp/work/pkg\n" in script
    assert "echo {} > cfg.json\nscip-python index ." in script
    assert "NODE_OPTIONS=--max-old-space-size=3072" in script
