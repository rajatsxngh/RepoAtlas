"""Building a snapshot never runs the repository's code (PRD §11): files are read as bytes from
git objects, parsed, and stored. A repository full of code that would leave a mark if anything
executed, imported, installed, or checked it out builds cleanly and leaves no mark."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from repoatlas.pipeline.local import build_local_snapshot
from repoatlas.store.graphstore import GraphStore
from tests.unit.api.helpers import api_settings

MARK = "REPOATLAS_EXECUTED_REPO_CODE"


def booby_trapped(root: Path, mark: Path) -> None:
    """A repository whose every auto-run hook would create ``mark``."""
    touch = f"import pathlib; pathlib.Path(r'{mark}').write_text('{MARK}')"
    files = {
        # Python: run on import, by pytest, by pip, by the interpreter at start-up.
        "setup.py": f"{touch}\nfrom setuptools import setup\nsetup(name='trap')\n",
        "conftest.py": f"{touch}\n",
        "sitecustomize.py": f"{touch}\n",
        "usercustomize.py": f"{touch}\n",
        "src/trap/__init__.py": f"{touch}\n\ndef work():\n    return 1\n",
        "src/trap/__main__.py": f"{touch}\n",
        "pyproject.toml": (
            "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'trap_backend'\n"
            "backend-path = ['.']\n[tool.pytest.ini_options]\naddopts = '-p trap_plugin'\n"
        ),
        "trap_backend.py": f"{touch}\n",
        "trap_plugin.py": f"{touch}\n",
        "tests/test_trap.py": f"{touch}\n\ndef test_trap():\n    assert True\n",
        # JavaScript and TypeScript: install scripts and config files that tools execute.
        "package.json": (
            '{"name": "trap", "scripts": {"preinstall": "node trap.js", "postinstall": '
            '"node trap.js", "prepare": "node trap.js"}}\n'
        ),
        "trap.js": f"require('fs').writeFileSync({str(mark)!r}, '{MARK}');\n",
        "web/index.ts": (
            f"import * as fs from 'fs';\nfs.writeFileSync({str(mark)!r}, '{MARK}');\n"
            "export function run(): number {\n  return 1;\n}\n"
        ),
        "tsconfig.json": '{"extends": "./trap.js"}\n',
        ".eslintrc.js": f"require('fs').writeFileSync({str(mark)!r}, '{MARK}');\n",
        # Git and shell: attributes, filters, hooks, and build files that run commands.
        ".gitattributes": "* filter=trap diff=trap merge=trap\n*.py text eol=crlf\n",
        ".gitmodules": '[submodule "x"]\n\tpath = x\n\turl = ext::sh -c "touch pwned"\n',
        ".githooks/post-checkout": f"#!/bin/sh\necho {MARK} > '{mark}'\n",
        "Makefile": f"all:\n\techo {MARK} > '{mark}'\n",
        "Dockerfile": f"FROM scratch\nRUN echo {MARK} > '{mark}'\n",
        ".envrc": f"echo {MARK} > '{mark}'\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


@pytest.mark.injection_static
def test_a_booby_trapped_repository_builds_without_running_anything(
    pg_engine: Engine, pg_url: str, tmp_path: Path, clean_store: None
) -> None:
    repo, mark = tmp_path / "trap", tmp_path / "executed.txt"
    booby_trapped(repo, mark)
    settings = api_settings(database_url=pg_url, data_dir=tmp_path / "data")
    built = build_local_snapshot(repo, settings, pg_engine, name="trap")

    assert not mark.exists(), mark.read_text() if mark.exists() else ""
    assert not Path("pwned").exists() and not (repo / "pwned").exists()
    # It was analyzed all the same: parsed, stored, and readable through the store.
    store = GraphStore.from_settings(settings, pg_engine)
    assert store.snapshot(built.snapshot_id).status == "ready"
    names = {match.node.qualified_name for match in store.find_symbols(built.snapshot_id, "work")}
    assert "work" in names
    code = store.read_code(built.snapshot_id, "src/trap/__init__.py", 1, 2)
    assert "pathlib" in code.text  # the text is stored as data
    assert store.find_symbols(built.snapshot_id, "run", limit=5)
    # No working tree was ever written: nothing of the repository exists under the data dir.
    leftovers = [
        p.name for p in (tmp_path / "data").rglob("*") if p.name in ("setup.py", "trap.js")
    ]
    assert leftovers == []
