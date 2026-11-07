"""The configuration fingerprint (T7): what changes it, what doesn't, and version pins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from repoatlas.config import Settings
from repoatlas.graph import build as graph_build
from repoatlas.graph.tiers import TIERS_VERSION
from repoatlas.ingest.source import MemorySourceTree, TreeEntry
from repoatlas.parsing import EXTRACTOR_VERSION
from repoatlas.pipeline import build as pipeline_build
from repoatlas.pipeline.build import (
    BUILD_STEPS,
    Step,
    TreeBlobs,
    config_fingerprint,
    fingerprint_parts,
)
from repoatlas.pipeline.fingerprint import compute
from repoatlas.pipeline.parse import parse_tree

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_compute_is_a_digest_of_canonical_json() -> None:
    assert compute({"a": 1, "b": [1, 2]}) == compute({"b": [1, 2], "a": 1})
    assert compute({"a": 1}) != compute({"a": 2})
    assert len(compute({})) == 64


def test_the_fingerprint_is_stable() -> None:
    assert config_fingerprint(settings()) == config_fingerprint(settings())
    steps = fingerprint_parts(settings())["steps"]
    assert isinstance(steps, list)
    assert [step["name"] for step in steps if isinstance(step, dict)] == [
        "resolve",
        "fetch",
        "inventory",
        "parse",
        "graph",
        "store",
    ]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_file_bytes", 2_000_000),
        ("max_source_files", 10),
        ("owner_commits", 5),
        ("history_depth", 3),
    ],
)
def test_settings_that_change_output_change_the_fingerprint(name: str, value: int) -> None:
    assert config_fingerprint(settings(**{name: value})) != config_fingerprint(settings())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("log_level", "DEBUG"),
        ("git_timeout_s", 5.0),
        ("github_token", SecretStr("token")),
        ("database_url", "postgresql+psycopg://other@db:5432/other"),
        ("data_dir", Path("elsewhere")),
        ("max_repo_size_kb", 1),
        ("graph_cache_mb", 1),
        ("max_read_lines", 10),
    ],
)
def test_settings_that_do_not_change_output_leave_it_alone(name: str, value: object) -> None:
    assert config_fingerprint(settings(**{name: value})) == config_fingerprint(settings())


def test_component_versions_change_the_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    before = config_fingerprint(settings())
    monkeypatch.setattr(graph_build, "GRAPH_VERSION", graph_build.GRAPH_VERSION + 1)
    after_graph = config_fingerprint(settings())
    monkeypatch.setattr(pipeline_build, "EXTRACTOR_VERSION", EXTRACTOR_VERSION + 1)
    after_extractor = config_fingerprint(settings())
    monkeypatch.setattr(pipeline_build, "grammar_versions", lambda: {"tree-sitter": "9.9.9"})
    after_grammar = config_fingerprint(settings())
    assert len({before, after_graph, after_extractor, after_grammar}) == 4


def test_adding_or_removing_a_step_changes_the_fingerprint() -> None:
    base = config_fingerprint(settings())
    extra = (*BUILD_STEPS, Step("embed", lambda ctx: None, ("max_file_bytes",)))
    assert config_fingerprint(settings(), extra) != base
    assert config_fingerprint(settings(), BUILD_STEPS[:-1]) != base


@pytest.mark.parametrize(
    ("kind", "version", "digests"),
    [
        (
            "parse",
            (EXTRACTOR_VERSION,),
            {"py_small": "31cf59ec616dfb6a", "ts_small": "13b0ed333b8a253e"},
        ),
        (
            "graph",
            (graph_build.GRAPH_VERSION, TIERS_VERSION),
            {"py_small": "ec01fa4353017159", "ts_small": "c066ffa9b52dbd10"},
        ),
    ],
)
def test_versions_move_with_the_goldens(
    kind: str, version: tuple[int, ...], digests: dict[str, str]
) -> None:
    """If a golden digest changed, the output changed: bump the matching version (so new
    snapshots get a new fingerprint), then update the digests and versions pinned here."""
    assert version == (1,) * len(version)
    for name, prefix in digests.items():
        golden = json.loads((GOLDEN / f"{name}.{kind}.json").read_text(encoding="utf-8"))
        assert golden["digest"].startswith(prefix), f"{name}.{kind}.json changed: bump the version"


def test_tree_blobs_read_only_what_is_asked_for() -> None:
    reads: list[str] = []

    class CountingTree(MemorySourceTree):
        def read(self, entry: TreeEntry) -> bytes:
            reads.append(entry.path)
            return super().read(entry)

    with CountingTree({"a.py": b"x = 1\n", "b.py": b"y = 2\n", "c.bin": b"\0\0"}) as tree:
        inventory = parse_tree(tree, max_source_files=10, max_file_bytes=1_000).inventory
        reads.clear()
        blobs = TreeBlobs(tree, inventory.indexed)
        assert len(blobs) == 2 and reads == []
        oid = next(entry.blob_oid for entry in inventory.indexed if entry.path == "b.py")
        assert blobs[oid] == b"y = 2\n" and reads == ["b.py"]
        assert set(blobs) == {entry.blob_oid for entry in inventory.indexed}
        with pytest.raises(KeyError):
            blobs["0" * 40]
