"""Round trip (T8): build → store → load gives back the same graph, digest for digest.

The fixture graphs and real builds are checked elsewhere (test_loader, the pipeline tests, and
scripts/bench_store.py); here seeded random graphs exercise every field and edge case the store
has to carry: ranges with and without columns, every kind and provenance, nested meta, Unicode
and percent-encoded keys, unresolved calls with candidates, and owners across time zones.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine

from repoatlas.graph.keys import dir_key, file_key, parent_dir
from repoatlas.graph.model import (
    CodeGraph,
    Edge,
    EdgeKind,
    GraphStats,
    Node,
    NodeKind,
    Owner,
    Provenance,
    UnresolvedCall,
)
from repoatlas.ingest.inventory import FileEntry, Inventory, InventoryStats
from repoatlas.ingest.source import blob_oid
from repoatlas.parsing.models import Range
from repoatlas.parsing.symbols import SymbolKind, make_symbol_key
from repoatlas.store.loader import load_graph, save_snapshot
from tests.integration.store.helpers import FINGERPRINT, SHA_A, repo_meta

pytestmark = pytest.mark.usefixtures("clean_store")

NAMES = ["run", "main", "__init__", "café", "größe", "名前", "a:b", "x~2", "50%", "with space"]
FOLDERS = ["src", "src/app", "tests", "docs/ünï", "a:b", "x y"]
REASONS = ["ambiguous", "not_found", "unbound_name", "dynamic"]
SYMBOL_KINDS = [NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.TEST]


def meta(rng: random.Random, depth: int = 0) -> dict[str, JsonValue]:
    values: list[JsonValue] = [
        rng.choice(NAMES),
        rng.randint(-5, 10**12),
        rng.random() < 0.5,
        None,
        round(rng.random(), 4),
        'line\nbreak\ttab "quote" \\ back',
    ]
    out: dict[str, JsonValue] = {}
    for position in range(rng.randint(0, 4)):
        value = rng.choice(values)
        if depth < 2 and rng.random() < 0.3:
            value = [rng.choice(values), meta(rng, depth + 1)]
        out[f"{rng.choice(NAMES)}{position}"] = value
    return out


def a_range(rng: random.Random) -> Range:
    start = rng.randint(1, 500)
    return Range(
        start_line=start,
        start_col=rng.randint(0, 120),
        end_line=start + rng.randint(0, 40),
        end_col=rng.randint(0, 120),
    )


def random_snapshot(seed: int) -> tuple[Inventory, dict[str, bytes], CodeGraph]:
    rng = random.Random(seed)
    nodes: dict[str, Node] = {}
    files: list[FileEntry] = []
    blobs: dict[str, bytes] = {}

    def add_dir(path: str) -> None:
        key = dir_key(path)
        if key in nodes:
            return
        parent = None if path == "." else parent_dir(path)
        if parent is not None:
            add_dir(parent)
        nodes[key] = Node(
            key=key,
            kind=NodeKind.DIR,
            name=path.rsplit("/", 1)[-1],
            qualified_name=path,
            path=path,
            parent_key=None if parent is None else dir_key(parent),
        )

    add_dir(".")
    for number in range(rng.randint(3, 9)):
        folder = rng.choice(FOLDERS)
        path = f"{folder}/{rng.choice(NAMES)}_{number}.py"
        add_dir(folder)
        data = f"# {path}\n".encode() + b"x = 1\n" * rng.randint(1, 30)
        skipped = rng.random() < 0.2
        files.append(
            FileEntry(
                path=path,
                mode="100644",
                blob_oid=blob_oid(data),
                size=len(data),
                language="py",
                is_test=folder == "tests",
                loc=None if skipped else data.count(b"\n"),
                skip_reason="generated" if skipped else None,
            )
        )
        if not skipped:
            blobs[blob_oid(data)] = data
        nodes[file_key(path)] = Node(
            key=file_key(path),
            kind=NodeKind.FILE,
            name=path.rsplit("/", 1)[-1],
            qualified_name=path,
            path=path,
            parent_key=dir_key(folder),
            meta=meta(rng),
        )
        parents = [file_key(path)]
        for position in range(0 if skipped else rng.randint(0, 6)):
            kind = rng.choice(SYMBOL_KINDS)
            qualname = f"{rng.choice(NAMES)}{position}"
            symbol_kind = cast(SymbolKind, kind.value)
            key = make_symbol_key("py", path, qualname, symbol_kind, rng.choice([1, 1, 2]))
            if key in nodes:
                continue
            nodes[key] = Node(
                key=key,
                kind=kind,
                name=qualname,
                qualified_name=qualname,
                path=path,
                name_range=a_range(rng),
                span=a_range(rng),
                parent_key=rng.choice(parents),
                exported=rng.random() < 0.5,
                meta=meta(rng),
            )
            parents.append(key)

    keys = list(nodes)
    edges = [
        Edge(
            src=rng.choice(keys),
            dst=rng.choice(keys),
            kind=rng.choice(list(EdgeKind)),
            provenance=rng.choice(list(Provenance)),
            confidence=rng.choice([1.0, 0.95, 0.15, 0.0001, rng.uniform(0.0001, 1)]),
            site=a_range(rng) if rng.random() < 0.7 else None,
            meta=meta(rng),
        )
        for _ in range(rng.randint(0, 60))
    ]
    unresolved = [
        UnresolvedCall(
            caller_key=rng.choice(keys),
            callee_text=rng.choice(["self.items.append", "f", "handlers[k]", "a.b().c", "é.ü"]),
            receiver_text=rng.choice([None, "self.items", "obj"]),
            site=a_range(rng),
            candidates=rng.sample(keys, rng.randint(0, min(5, len(keys)))),
            reason=rng.choice(REASONS),  # type: ignore[arg-type]
        )
        for _ in range(rng.randint(0, 10))
    ]
    owners: dict[str, list[Owner]] = {}
    for entry in rng.sample(files, rng.randint(0, len(files))):
        authors = rng.randint(1, 3)
        owners[entry.path] = [
            Owner(
                author_name=f"{rng.choice(NAMES)} {author}",
                author_hash=f"{rng.getrandbits(64):016x}",
                commits=rng.randint(1, 50),
                last_commit_at=datetime(2020, 1, 1, tzinfo=UTC)
                + timedelta(seconds=rng.randint(0, 10**8)),
                share=round(1 / authors, 4),
            )
            for author in range(authors)
        ]
        ist = timezone(timedelta(hours=5, minutes=30))
        first = owners[entry.path][0]
        owners[entry.path][0] = Owner(
            author_name=first.author_name,
            author_hash=first.author_hash,
            commits=first.commits,
            last_commit_at=first.last_commit_at.astimezone(ist),
            share=first.share,
        )
    stats = GraphStats(
        nodes_by_kind=dict(Counter(node.kind.value for node in nodes.values())),
        edges_by_kind=dict(Counter(edge.kind.value for edge in edges)),
        unresolved_calls=len(unresolved),
    )
    graph = CodeGraph(nodes=nodes, edges=edges, unresolved=unresolved, owners=owners, stats=stats)
    indexed = [entry for entry in files if entry.skip_reason is None]
    inventory = Inventory(
        files=sorted(files, key=lambda entry: entry.path),
        stats=InventoryStats(
            tree_entries=len(files),
            source_files=len(files),
            indexed=len(indexed),
            indexed_by_language={"py": len(indexed)} if indexed else {},
            skipped={"generated": len(files) - len(indexed)} if len(indexed) < len(files) else {},
        ),
    )
    return inventory, blobs, graph


@pytest.mark.parametrize("seed", range(12))
def test_random_graphs_come_back_identical(pg_engine: Engine, seed: int) -> None:
    inventory, blobs, graph = random_snapshot(seed)
    snapshot_id = save_snapshot(
        repo_meta("acme", f"random-{seed}"),
        SHA_A,
        FINGERPRINT,
        inventory,
        blobs,
        graph,
        engine=pg_engine,
    )
    loaded = load_graph(snapshot_id, engine=pg_engine)
    assert loaded == graph
    assert loaded.to_json() == graph.to_json()
    assert loaded.digest() == graph.digest()


def test_an_empty_graph_round_trips(pg_engine: Engine) -> None:
    graph = CodeGraph(
        nodes={
            "dir:.": Node(key="dir:.", kind=NodeKind.DIR, name=".", qualified_name=".", path=".")
        }
    )
    inventory = Inventory(
        files=[],
        stats=InventoryStats(
            tree_entries=0, source_files=0, indexed=0, indexed_by_language={}, skipped={}
        ),
    )
    snapshot_id = save_snapshot(
        repo_meta("acme", "empty"), SHA_A, FINGERPRINT, inventory, {}, graph, engine=pg_engine
    )
    assert load_graph(snapshot_id, engine=pg_engine).digest() == graph.digest()
