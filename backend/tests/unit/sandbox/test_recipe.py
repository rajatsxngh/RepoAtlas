from __future__ import annotations

from pathlib import Path

import pytest

from repoatlas.sandbox.recipe import RecipeError, load_recipe, parse_recipe, write_quarantine

SHA = "a" * 40

VALID = f"""\
id: click
url: https://github.com/pallets/click
commit: {SHA}
language: python
base_image: python
install: ["uv pip install -r requirements.txt"]
test:
  cmd: "pytest -q --junitxml=/tmp/out/report"
  reporter: junit
  timeout_s: 900
scip:
  indexer: scip-python
  args: ["index", "."]
quarantine: []
"""


def test_valid_recipe_loads() -> None:
    recipe = parse_recipe(VALID)
    assert recipe.id == "click"
    assert recipe.clone_url == "https://github.com/pallets/click.git"
    assert recipe.test.selection == "nodeids"
    assert recipe.workdir == "src"
    assert len(recipe.digest()) == 64


def test_digest_changes_with_content() -> None:
    assert parse_recipe(VALID).digest() != parse_recipe(VALID.replace("900", "901")).digest()


def test_unquoted_numeric_commit_gets_a_hint() -> None:
    with pytest.raises(RecipeError, match="quoted string"):
        parse_recipe(VALID.replace(f"commit: {SHA}", "commit: " + "1" * 40))


def test_json_reporter_selects_files() -> None:
    recipe = parse_recipe(VALID.replace("reporter: junit", "reporter: vitest-json"))
    assert recipe.test.selection == "files"


@pytest.mark.parametrize(
    ("old", "new", "line", "where"),
    [
        (f"commit: {SHA}", "commit: main", 3, "commit"),
        ("language: python", "language: rust", 4, "language"),
        ("  timeout_s: 900", "  timeout_s: 1", 10, "test.timeout_s"),
        ("  indexer: scip-python", "  indexer: scip-java", 12, "scip.indexer"),
        ("url: https://github.com/pallets/click", "url: http://example.com/x", 2, "url"),
    ],
)
def test_validation_errors_name_the_line(old: str, new: str, line: int, where: str) -> None:
    with pytest.raises(RecipeError) as info:
        parse_recipe(VALID.replace(old, new), source="click.yaml")
    assert f"click.yaml:{line}: {where}:" in str(info.value)


def test_unknown_key_is_rejected_with_its_line() -> None:
    with pytest.raises(RecipeError) as info:
        parse_recipe(VALID + "colour: blue\n", source="r.yaml")
    assert "r.yaml:15: colour:" in str(info.value)


def test_missing_key_points_at_the_parent() -> None:
    text = VALID.replace("  reporter: junit\n", "")
    with pytest.raises(RecipeError) as info:
        parse_recipe(text, source="r.yaml")
    assert "r.yaml:8: test.reporter: Field required" in str(info.value)


def test_invalid_yaml_reports_line() -> None:
    with pytest.raises(RecipeError) as info:
        parse_recipe("id: x\ntest: [unclosed\n", source="r.yaml")
    assert "r.yaml:" in str(info.value)
    assert "invalid YAML" in str(info.value)


def test_report_path_must_be_under_out() -> None:
    text = VALID.replace("  timeout_s: 900", "  timeout_s: 900\n  report_path: /etc/passwd")
    with pytest.raises(RecipeError, match="report_path"):
        parse_recipe(text)


def test_load_recipe_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "click.yaml"
    path.write_text(VALID, encoding="utf-8")
    assert load_recipe(path).id == "click"


def test_write_quarantine_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "click.yaml"
    path.write_text(
        "# header comment\n" + VALID.replace("quarantine: []\n", "quarantine: []\nenv: {}\n")
    )
    write_quarantine(path, ["tests/b.py::test_b", "tests/a.py::test_a[x y]"])
    text = path.read_text()
    assert text.startswith("# header comment\n")
    assert '  - "tests/a.py::test_a[x y]"\n  - "tests/b.py::test_b"\nenv: {}\n' in text
    assert load_recipe(path).quarantine == ["tests/a.py::test_a[x y]", "tests/b.py::test_b"]

    write_quarantine(path, [])
    assert "quarantine: []\nenv: {}\n" in path.read_text()
    assert load_recipe(path).quarantine == []
