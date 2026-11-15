"""``scripts/check_links.py``: relative links and heading anchors are checked without the
network, web links only when asked, and the repository's own docs pass."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_links.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_links"] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


links = load()


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def broken(root: Path, *files: Path) -> list[str]:
    problems, _, _ = links.check_files(files, root)
    return [f"{problem.link.target}: {problem.message}" for problem in problems]


def test_the_repository_docs_have_no_broken_relative_link() -> None:
    files = links.default_files(ROOT)
    assert ROOT / "README.md" in files
    assert ROOT / "docs" / "results.md" in files

    problems, _, checked = links.check_files(files, ROOT)

    assert [problem.render(ROOT) for problem in problems] == []
    assert checked > 100


def test_relative_links_must_exist(tmp_path: Path) -> None:
    write(tmp_path, "docs/guide.md", "# Guide\n")
    write(tmp_path, "img/map.png", "png")
    readme = write(
        tmp_path,
        "README.md",
        "[guide](docs/guide.md) [folder](docs/) ![map](img/map.png) [gone](docs/missing.md)\n"
        "[spaced](<docs/my file.md>) [encoded](docs/my%20file.md)\n",
    )
    assert broken(tmp_path, readme) == [
        "docs/missing.md: no such file",
        "docs/my file.md: no such file",
        "docs/my%20file.md: no such file",
    ]
    write(tmp_path, "docs/my file.md", "# Spaced\n")
    assert broken(tmp_path, readme) == ["docs/missing.md: no such file"]


def test_fragments_must_name_a_heading(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/guide.md",
        "# The `repoatlas` CLI: what's new?\n\n## Setup\n\n## Setup\n\n"
        '<a name="custom"></a>\n\n```\n# not a heading\n```\n\n### [Linked](x.md) title\n',
    )
    readme = write(
        tmp_path,
        "README.md",
        "# Top\n\n## Live pass (pending; not part of this gate)\n\n"
        "[a](docs/guide.md#the-repoatlas-cli-whats-new) [b](docs/guide.md#setup) "
        "[c](docs/guide.md#setup-1) [d](docs/guide.md#custom) [e](docs/guide.md#linked-title)\n"
        "[f](#live-pass-pending-not-part-of-this-gate) [g](#top) [h](docs/guide.md#Setup)\n"
        "[bad](docs/guide.md#not-a-heading) [bad2](#missing) [bad3](docs/guide.md#setup-2)\n",
    )
    assert broken(tmp_path, readme) == [
        "docs/guide.md#not-a-heading: no heading for #not-a-heading",
        "#missing: no heading for #missing",
        "docs/guide.md#setup-2: no heading for #setup-2",
    ]


def test_code_is_not_a_link_and_paths_stay_inside_the_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(tmp_path, "outside.md", "# Outside\n")
    readme = write(
        root,
        "README.md",
        "`[not](a/link.md)` and ``[also](not.md)``\n\n```bash\n[nor](this.md)\n```\n\n"
        "~~~\n[nor](that.md)\n~~~\n\n[up](../outside.md) [root](/docs/x.md)\n"
        "[mail](mailto:someone@example.invalid)\n\n[ref]: missing-ref.md\n",
    )
    assert broken(root, readme) == [
        "../outside.md: points outside the repository",
        "/docs/x.md: use a relative path (a path starting with / means something else on GitHub)",
        "missing-ref.md: no such file",
    ]


def test_web_links_are_requested_only_when_asked(tmp_path: Path) -> None:
    readme = write(
        tmp_path,
        "README.md",
        "[ok](https://example.invalid/ok#part) [ok again](https://example.invalid/ok)\n"
        "[gone](https://example.invalid/gone) <https://example.invalid/down>\n"
        "[shy](https://example.invalid/shy)\n",
    )
    asked: list[str] = []
    answers = {
        "https://example.invalid/ok": 200,
        "https://example.invalid/gone": 404,
        "https://example.invalid/down": 0,
        "https://example.invalid/shy": 403,
    }

    def status_of(url: str) -> int:
        asked.append(url)
        return answers[url]

    offline = links.check_files([readme], tmp_path, status_of=status_of)
    assert offline == ([], [], 0) and asked == []

    problems, unverified, checked = links.check_files(
        [readme], tmp_path, external=True, status_of=status_of
    )
    assert [f"{p.link.target}: {p.message}" for p in problems] == [
        "https://example.invalid/gone: HTTP 404",
        "https://example.invalid/down: no response",
    ]
    assert [p.link.target for p in unverified] == ["https://example.invalid/shy"]
    assert checked == 5
    assert asked == sorted(set(asked), key=asked.index)  # each URL once
    assert len(asked) == 4


def test_the_command_exits_with_1_on_a_broken_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write(tmp_path, "README.md", "# Title\n\n[self](#title)\n")
    assert links.main(["--root", str(tmp_path), str(good)]) == 0
    assert "OK: 1 relative links in 1 files." in capsys.readouterr().out

    bad = write(tmp_path, "docs/bad.md", "[gone](nowhere.md)\n")
    assert links.main(["--root", str(tmp_path), str(bad)]) == 1
    assert "BROKEN docs/bad.md:1: nowhere.md: no such file" in capsys.readouterr().err
    assert links.main(["--root", str(tmp_path)]) == 1  # the default set finds it too
    assert links.main(["--root", str(tmp_path), str(tmp_path / "absent.md")]) == 2
