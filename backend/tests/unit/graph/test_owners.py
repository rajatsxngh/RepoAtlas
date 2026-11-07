from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from repoatlas.graph.build import GraphSettings, build_graph
from repoatlas.graph.owners import (
    CommitLog,
    CommitRecord,
    author_hash,
    compute_owners,
    parse_commit_log,
    read_commit_log,
)
from repoatlas.ingest.fetch import fetch_history
from repoatlas.ingest.git import run_git
from repoatlas.ingest.source import MemorySourceTree
from repoatlas.pipeline.parse import parse_tree
from tests.unit.ingest.gitrepo import OriginRepo, Spec

ADA = ("Ada Lovelace", "ada@example.com")
GRACE = ("Grace Hopper", "grace@example.com")


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


def commit_as(
    origin: OriginRepo,
    monkeypatch: pytest.MonkeyPatch,
    who: tuple[str, str],
    seconds: int,
    files: dict[str, bytes],
) -> str:
    name, email = who
    for prefix in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{prefix}_NAME", name)
        monkeypatch.setenv(f"{prefix}_EMAIL", email)
        monkeypatch.setenv(f"{prefix}_DATE", f"@{seconds} +0000")
    return origin.commit([Spec(path, data) for path, data in files.items()])


@pytest.fixture
def history_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OriginRepo:
    origin = OriginRepo(tmp_path / "origin.git")
    files = {"src/a.py": b"a = 1\n", "src/b.py": b"b = 1\n", "docs/readme.md": b"hi\n"}
    commit_as(origin, monkeypatch, ADA, 1_000, files)
    commit_as(origin, monkeypatch, GRACE, 2_000, {**files, "src/a.py": b"a = 2\n"})
    files = {**files, "src/a.py": b"a = 3\n", "src/b.py": b"b = 2\n"}
    commit_as(origin, monkeypatch, ("Ada L.", " ADA@Example.com "), 3_000, files)
    files = {**files, "src/c.py": b"c = 1\n", "src/ü dir/x y.py": b"x = 1\n"}
    commit_as(origin, monkeypatch, GRACE, 4_000, files)
    return origin


def test_owners_from_a_scripted_history(history_origin: OriginRepo, tmp_path: Path) -> None:
    head = history_origin.head
    assert head is not None
    store = fetch_history(history_origin.url, head, tmp_path / "history.git", depth=10)
    log = read_commit_log(store)
    assert [c.author_name for c in log.commits] == [
        "Grace Hopper",
        "Ada L.",
        "Grace Hopper",
        "Ada Lovelace",
    ]
    assert log.commits[0].paths == ("src/c.py", "src/ü dir/x y.py")
    assert log.commits[-1].paths == ("docs/readme.md", "src/a.py", "src/b.py")
    assert log.commits[1].author_hash == author_hash("ada@example.com")
    kinds = run_git(
        ["cat-file", "--batch-all-objects", "--batch-check=%(objecttype)"], cwd=store.path
    ).split()
    assert kinds.count(b"blob") == 0  # reading the log downloaded no file contents

    owners = compute_owners(log, ["src/a.py", "src/b.py", "src/c.py", "src/ü dir/x y.py"])
    assert set(owners) == {"src/a.py", "src/b.py", "src/c.py", "src/ü dir/x y.py"}
    ada, grace = owners["src/a.py"]
    assert (ada.author_name, ada.commits, ada.last_commit_at, ada.share) == (
        "Ada L.",
        2,
        at(3_000),
        0.6667,
    )
    assert (grace.author_name, grace.commits, grace.last_commit_at, grace.share) == (
        "Grace Hopper",
        1,
        at(2_000),
        0.3333,
    )
    assert [(o.author_name, o.commits, o.share) for o in owners["src/b.py"]] == [("Ada L.", 2, 1.0)]


def test_the_shallow_boundary_commit_lists_no_files(
    history_origin: OriginRepo, tmp_path: Path
) -> None:
    head = history_origin.head
    assert head is not None
    store = fetch_history(history_origin.url, head, tmp_path / "history.git", depth=2)
    log = read_commit_log(store)
    assert [c.author_name for c in log.commits] == ["Grace Hopper", "Ada L."]
    assert log.commits[1].paths == ()  # its parent is missing: git would list every file
    assert log.commits[0].paths == ("src/c.py", "src/ü dir/x y.py")


def test_the_commit_limit(history_origin: OriginRepo, tmp_path: Path) -> None:
    head = history_origin.head
    assert head is not None
    store = fetch_history(history_origin.url, head, tmp_path / "history.git", depth=10)
    assert len(read_commit_log(store, limit=1).commits) == 1
    assert read_commit_log(store, limit=0).commits == ()


def test_build_graph_attaches_owners_and_never_stores_emails(
    history_origin: OriginRepo, tmp_path: Path
) -> None:
    head = history_origin.head
    assert head is not None
    store = fetch_history(history_origin.url, head, tmp_path / "history.git", depth=10)
    log = read_commit_log(store)
    sources: dict[str, bytes | tuple[str, bytes]] = {
        "src/a.py": b"a = 3\n",
        "src/b.py": b"b = 2\n",
        "src/c.py": b"c = 1\n",
    }
    with MemorySourceTree(sources) as tree:
        output = parse_tree(tree, max_source_files=10, max_file_bytes=10_000)
    graph = build_graph(output.inventory, output.files, log, GraphSettings(owner_commits=2))
    assert set(graph.owners) == {"src/a.py", "src/b.py", "src/c.py"}  # only inventoried files
    assert [o.commits for o in graph.owners["src/a.py"]] == [1]  # the two newest commits only
    assert graph.stats.history_commits == 2
    assert graph.stats.files_with_owners == 3
    text = graph.to_json()
    assert "example.com" not in text.lower()
    assert author_hash("grace@example.com") in text


def test_parse_commit_log_handles_quoting_merges_and_noise() -> None:
    output = (
        "\x1e" + "a" * 40 + "\x1fAda\x1fada@x.io\x1f100\n\n"
        '"tab\\there.py"\n"caf\\303\\251.py"\nplain.py\nplain.py\n'
        "\x1e" + "b" * 40 + "\x1fMerge Bot\x1fbot@x.io\x1f50\n"
        "\x1enot a header\n"
    )
    log = parse_commit_log(output)
    assert [c.sha[0] for c in log.commits] == ["a", "b"]
    assert log.commits[0].paths == ("tab\there.py", "café.py", "plain.py")
    assert log.commits[1].paths == ()
    assert log.commits[1].committed_at == at(50)
    assert parse_commit_log(output, shallow=["a" * 40]).commits[0].paths == ()


def test_author_hashes_ignore_case_and_whitespace() -> None:
    assert author_hash(" Ada@Example.COM ") == author_hash("ada@example.com")
    assert len(author_hash("ada@example.com")) == 16
    assert author_hash("ada@example.com") != author_hash("grace@example.com")


def test_compute_owners_orders_by_commits_then_recency() -> None:
    def record(sha: str, who: str, seconds: int, *paths: str) -> CommitRecord:
        return CommitRecord(
            sha=sha, author_name=who, author_hash=who.lower(), committed_at=at(seconds), paths=paths
        )

    log = CommitLog(
        commits=(
            record("4", "Cy", 400, "x.py"),
            record("3", "Bo", 300, "x.py", "gone.py"),
            record("2", "Al", 200, "x.py"),
            record("1", "Bo", 100, "x.py"),
        )
    )
    owners = compute_owners(log, ["x.py"])
    assert [(o.author_name, o.commits, o.share) for o in owners["x.py"]] == [
        ("Bo", 2, 0.5),
        ("Cy", 1, 0.25),
        ("Al", 1, 0.25),
    ]
    assert owners["x.py"][0].last_commit_at == at(300)
    assert compute_owners(log, ["x.py"], limit=1)["x.py"][0].author_name == "Cy"
    assert compute_owners(CommitLog(), ["x.py"]) == {}
