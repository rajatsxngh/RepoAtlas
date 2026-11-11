from __future__ import annotations

import io
import tarfile

from repoatlas.ingest.source import SourceTree, TreeEntry
from repoatlas.sandbox.images import install_script, source_context, upstream_refs
from tests.integration.sandbox.fixtures import minipy_recipe


class FakeTree(SourceTree):
    label = "fake"
    commit = None

    def __init__(self) -> None:
        self.files = {
            "a.py": ("100644", b"print(1)\n"),
            "bin/run": ("100755", b"#!/bin/sh\n"),
            "link": ("120000", b"a.py"),
            "vendor/sub": ("160000", b""),
        }

    def entries(self) -> list[TreeEntry]:
        return [
            TreeEntry(path=p, mode=m, oid="0" * 40, size=len(d))
            for p, (m, d) in sorted(self.files.items())
        ]

    def read(self, entry: TreeEntry) -> bytes:
        return self.files[entry.path][1]


def test_source_context_keeps_modes_and_symlinks_and_skips_submodules() -> None:
    data, count = source_context(FakeTree(), "FROM x\n", overrides={"a.py": b"mutated\n"})
    assert count == 3
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == {"Dockerfile", "src/a.py", "src/bin/run", "src/link"}
        assert members["src/bin/run"].mode == 0o755
        assert members["src/a.py"].mode == 0o644
        assert members["src/link"].issym() and members["src/link"].linkname == "a.py"
        assert all(m.mtime == 0 and m.uid == 10001 for m in members.values())
        handle = tar.extractfile("src/a.py")
        assert handle is not None and handle.read() == b"mutated\n"


def test_source_context_is_deterministic() -> None:
    assert source_context(FakeTree(), "FROM x\n") == source_context(FakeTree(), "FROM x\n")


def test_install_script_exports_the_proxy_inside_the_script() -> None:
    script = install_script(minipy_recipe())
    assert script.startswith("set -eu\nexport ")
    assert "HTTPS_PROXY=http://repoatlas-sbx-proxy:3128" in script
    assert script.endswith("uv pip install -r requirements.txt")


def test_upstream_refs() -> None:
    text = "FROM a@sha256:1 AS uv\nRUN x\nfrom b@sha256:2\n"
    assert upstream_refs(text) == ["a@sha256:1", "b@sha256:2"]
