from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.fetch import Workspace, fetch_history, fetch_source, remove_tree
from repoatlas.ingest.git import run_git
from repoatlas.ingest.source import DirectorySourceTree, TreeEntry, blob_oid
from tests.unit.ingest.gitrepo import OriginRepo, Spec

TRICKY_FILES = [
    Spec("app/main.py", b"print('hi')\r\nprint('crlf stays')\r\n"),
    Spec("scripts/run.sh", b"#!/bin/sh\necho hi\n", mode="100755"),
    Spec("link.py", b"app/main.py", mode="120000"),
    Spec("vendor/sub", mode="160000", gitlink="a" * 40),
    Spec("aux.py", b"# a name Windows can't check out\n"),
    Spec("Foo.py", b"upper = 1\n"),
    Spec("foo.py", b"lower = 1\n"),
    Spec("docs/ünïcode name.py", "x = 'ü'\n".encode()),
]


@pytest.fixture
def origin(tmp_path: Path) -> OriginRepo:
    return OriginRepo(tmp_path / "origin.git")


def test_blob_oid_matches_git() -> None:
    assert blob_oid(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert blob_oid(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_fetch_source_reads_exact_bytes_without_a_working_tree(
    origin: OriginRepo, tmp_path: Path
) -> None:
    commit = origin.commit(TRICKY_FILES)
    dest = tmp_path / "src.git"
    with fetch_source(origin.url, commit, dest, label="test/origin") as tree:
        entries = {entry.path: entry for entry in tree.entries()}
        assert list(entries) == sorted(entries)
        assert set(entries) == {spec.path for spec in TRICKY_FILES}

        main = entries["app/main.py"]
        data = tree.read(main)
        assert data == b"print('hi')\r\nprint('crlf stays')\r\n"
        assert blob_oid(data) == main.oid
        assert main.size == len(data)

        assert entries["scripts/run.sh"].mode == "100755"
        assert entries["link.py"].mode == "120000"
        assert entries["vendor/sub"] == TreeEntry("vendor/sub", "160000", "a" * 40, None)
        assert tree.read(entries["aux.py"]).startswith(b"# a name")
        assert tree.read(entries["Foo.py"]) != tree.read(entries["foo.py"])
        assert tree.read(entries["docs/ünïcode name.py"]) == "x = 'ü'\n".encode()

    # Bare repository only: no checked-out files anywhere.
    assert not (dest / "app").exists()
    assert (dest / "HEAD").is_file()


def test_reading_a_submodule_entry_fails(origin: OriginRepo, tmp_path: Path) -> None:
    commit = origin.commit(TRICKY_FILES)
    with fetch_source(origin.url, commit, tmp_path / "src.git", label="t") as tree:
        submodule = next(e for e in tree.entries() if e.mode == "160000")
        with pytest.raises(FetchError, match="not a file"):
            tree.read(submodule)


def test_fetch_source_rejects_unknown_commits(origin: OriginRepo, tmp_path: Path) -> None:
    origin.commit([Spec("a.py", b"a = 1\n")])
    with pytest.raises(FetchError, match="git fetch failed"):
        fetch_source(origin.url, "b" * 40, tmp_path / "src.git", label="t")


@pytest.mark.parametrize("commit", ["main", "HEAD", "--upload-pack=touch pwned", "a" * 39])
def test_fetch_requires_a_full_sha(commit: str, tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="40-character commit SHA"):
        fetch_source("https://github.com/a/b.git", commit, tmp_path / "x", label="t")


@pytest.mark.parametrize(
    "url", ["ext::sh -c touch% pwned", "http://github.com/a/b.git", "ssh://git@github.com/a/b"]
)
def test_fetch_only_allows_github_https_and_file_urls(url: str, tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="can be fetched"):
        fetch_source(url, "a" * 40, tmp_path / "x", label="t")


def test_fetch_history_is_blobless(origin: OriginRepo, tmp_path: Path) -> None:
    for number in range(3):
        origin.commit([Spec("a.py", f"a = {number}\n".encode())], message=f"c{number}")
    assert origin.head is not None

    history = fetch_history(origin.url, origin.head, tmp_path / "history.git", depth=10)

    assert history.commit_count() == 3
    kinds = run_git(
        ["cat-file", "--batch-all-objects", "--batch-check=%(objecttype)"], cwd=history.path
    ).split()
    assert kinds.count(b"blob") == 0
    assert kinds.count(b"commit") == 3
    log = history.git("log", "--no-renames", "--name-only", "--format=%H", history.commit)
    assert log.count("a.py") == 3


def test_fetch_history_respects_depth(origin: OriginRepo, tmp_path: Path) -> None:
    for number in range(4):
        origin.commit([Spec("a.py", f"a = {number}\n".encode())])
    assert origin.head is not None
    history = fetch_history(origin.url, origin.head, tmp_path / "history.git", depth=2)
    assert history.commit_count() == 2


def test_directory_source_tree(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_bytes(b"x = 1\n")
    (root / "README.md").write_bytes(b"# readme\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_bytes(b"ignored")

    with DirectorySourceTree(root, label="fixture") as tree:
        entries = tree.entries()
        assert [entry.path for entry in entries] == ["README.md", "pkg/mod.py"]
        assert entries[1] == TreeEntry("pkg/mod.py", "100644", blob_oid(b"x = 1\n"), 6)
        assert tree.read(entries[1]) == b"x = 1\n"
        assert tree.label == "fixture"
        assert tree.commit is None


def test_directory_source_tree_reports_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "real.py").write_bytes(b"x = 1\n")
    try:
        os.symlink("real.py", root / "alias.py")
    except OSError:
        pytest.skip("Creating symlinks needs extra privileges on this system.")
    entries = {entry.path: entry for entry in DirectorySourceTree(root).entries()}
    assert entries["alias.py"].mode == "120000"
    assert entries["alias.py"].oid == blob_oid(b"real.py")


def test_remove_tree_handles_read_only_files(tmp_path: Path) -> None:
    target = tmp_path / "victim"
    (target / "objects").mkdir(parents=True)
    pack = target / "objects" / "pack.idx"
    pack.write_bytes(b"data")
    os.chmod(pack, stat.S_IREAD)
    remove_tree(target)
    assert not target.exists()


def test_workspace_cleans_up_unless_kept(tmp_path: Path) -> None:
    with Workspace(tmp_path / "work") as workspace:
        (workspace.path / "file").write_bytes(b"x")
        created = workspace.path
    assert not created.exists()

    with Workspace(tmp_path / "work", keep=True) as kept:
        pass
    assert kept.path.exists()
