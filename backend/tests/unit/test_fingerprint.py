"""The configuration fingerprint: what changes it, what doesn't, and version pins."""

from __future__ import annotations

import json
from collections.abc import Iterator
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
from repoatlas.pipeline.build import TreeBlobs
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.fingerprint import compute
from repoatlas.pipeline.parse import parse_tree
from repoatlas.pipeline.stages import (
    Stage,
    config_fingerprint,
    fingerprint_parts,
    register_stage,
    registered_stages,
    unregister_stage,
)

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def nothing(_ctx: BuildContext) -> None:
    pass


@pytest.fixture
def dummy() -> Iterator[Stage]:
    """A stage registered for the test, removed again afterwards."""
    stage = Stage("dummy", 1, ("max_file_bytes",), nothing)
    register_stage(stage)
    yield stage
    unregister_stage("dummy")


def test_compute_is_a_digest_of_canonical_json() -> None:
    assert compute({"a": 1, "b": [1, 2]}) == compute({"b": [1, 2], "a": 1})
    assert compute({"a": 1}) != compute({"a": 2})
    assert len(compute({})) == 64


def test_the_fingerprint_covers_the_registered_stages_in_order() -> None:
    assert config_fingerprint(settings()) == config_fingerprint(settings())
    stages = fingerprint_parts(settings())["stages"]
    assert isinstance(stages, list)
    assert [stage["name"] for stage in stages if isinstance(stage, dict)] == [
        "fetch",
        "inventory",
        "parse",
        "graph",
        "store",
        "chunk",
        "publish",
    ]
    fetch = stages[0]
    assert fetch == {
        "name": "fetch",
        "version": 1,
        "components": {},
        "settings": {"history_depth": 1000, "owner_commits": 1000},
    }
    assert [stage.name for stage in registered_stages()] == [
        "fetch",
        "inventory",
        "parse",
        "graph",
        "store",
        "chunk",
        "publish",
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
        ("job_lease_s", 5.0),
        ("job_max_attempts", 1),
        ("web_origins", "https://example.com"),
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


def test_registering_a_dummy_stage_changes_the_fingerprint() -> None:
    base = config_fingerprint(settings())
    register_stage(Stage("dummy", 1, (), nothing))
    try:
        with_dummy = config_fingerprint(settings())
        assert with_dummy != base
        assert [stage.name for stage in registered_stages()][-1] == "dummy"
    finally:
        unregister_stage("dummy")
    assert config_fingerprint(settings()) == base


def test_stage_versions_settings_order_and_removal_change_the_fingerprint(
    dummy: Stage,
) -> None:
    stages = registered_stages()
    base = config_fingerprint(settings(), stages)
    bumped = tuple(
        Stage(s.name, s.version + 1, s.settings_keys, s.run) if s is dummy else s for s in stages
    )
    no_settings = tuple(Stage(s.name, 1, (), s.run) if s is dummy else s for s in stages)
    reordered = (stages[-1], *stages[:-1])
    variants = [bumped, no_settings, reordered, stages[:-1]]
    fingerprints = {config_fingerprint(settings(), variant) for variant in variants}
    assert len(fingerprints | {base}) == len(variants) + 1
    assert config_fingerprint(settings(max_file_bytes=1), stages) != base  # dummy's setting


def test_stages_register_in_place(dummy: Stage) -> None:
    after_parse = Stage("after-parse", 1, (), nothing)
    register_stage(after_parse, after="parse")
    try:
        assert [stage.name for stage in registered_stages()] == [
            "fetch",
            "inventory",
            "parse",
            "after-parse",
            "graph",
            "store",
            "chunk",
            "publish",
            "dummy",
        ]
        with pytest.raises(ValueError, match="already registered"):
            register_stage(Stage("parse", 2, (), nothing))
        with pytest.raises(ValueError, match="No stage named"):
            register_stage(Stage("orphan", 1, (), nothing), after="nowhere")
    finally:
        unregister_stage("after-parse")
    assert "orphan" not in [stage.name for stage in registered_stages()]


def test_stages_are_validated_and_secrets_never_enter_fingerprints() -> None:
    with pytest.raises(ValueError, match="name"):
        Stage("", 1, (), nothing)
    with pytest.raises(ValueError, match="version"):
        Stage("x", 0, (), nothing)
    with pytest.raises(ValueError, match="weight"):
        Stage("x", 1, (), nothing, weight=0)
    leaky = Stage("leaky", 1, ("github_token",), nothing)
    with pytest.raises(ValueError, match="Secret"):
        fingerprint_parts(settings(github_token=SecretStr("t")), [leaky])
    paths = Stage("paths", 1, ("data_dir", "job_retry_backoff_s"), nothing)
    parts = fingerprint_parts(settings(data_dir=Path("a") / "b"), [paths])
    assert parts["stages"] == [
        {
            "name": "paths",
            "version": 1,
            "components": {},
            "settings": {"data_dir": "a/b", "job_retry_backoff_s": [10.0, 60.0, 300.0]},
        }
    ]


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
