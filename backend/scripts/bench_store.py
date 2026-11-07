"""Time graph-store reads on a stored snapshot (M03 T8). Not part of CI.

Usage (from backend/, with Postgres up and a snapshot built by ``repoatlas build``):
    uv run python scripts/bench_store.py --snapshot <id> [--samples 40] [--rounds 3] [--seed 3]

Measures, on this machine:
- the cold load of the snapshot's adjacency into an empty cache;
- the round trip: the graph read back from the store must have the digest recorded at save time;
- ``callers`` to depth 3 (warm cache) for the most-called symbols and a seeded random sample;
- ``subgraph`` (warm adjacency) for the root and the largest directories, at directory and file
  level, and for the largest files at symbol level: the first call aggregates, repeat calls are
  memoized;
- ``find_symbols``, ``get_node``, ``locate``, and ``read_code``.

Every time is wall-clock per call and includes the Postgres queries that fetch the nodes and
edges a call returns. Warm reads run once untimed per target, then ``--rounds`` timed times, and
are summarized over all timed calls. Prints a Markdown table (``--json`` writes the numbers).
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import statistics
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from repoatlas.config import get_settings
from repoatlas.db.engine import create_db_engine
from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.store.cache import Level
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import load_graph

SYMBOLS = {NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.TEST}


def timed[T](call: Callable[[], T]) -> tuple[float, T]:
    started = time.perf_counter()
    result = call()
    return (time.perf_counter() - started) * 1000, result


def repeated(call: Callable[[], object], rounds: int) -> list[float]:
    """One untimed call (first touch of the rows), then ``rounds`` timed ones."""
    call()
    return [timed(call)[0] for _ in range(rounds)]


def summary(name: str, times: list[float], note: str = "") -> dict[str, Any]:
    ordered = sorted(times)
    return {
        "operation": name,
        "calls": len(ordered),
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))],
        "max_ms": ordered[-1],
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=int, required=True)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--json", type=Path, default=None, help="Also write the numbers here.")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    rounds: int = args.rounds
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    store = GraphStore.from_settings(settings, engine)
    snapshot_id: int = args.snapshot
    info = store.snapshot(snapshot_id)
    stats: dict[str, Any] = dict(info.stats)
    rows: list[dict[str, Any]] = []

    load_ms, graph = timed(partial(store.graph, snapshot_id))
    rows.append(
        summary(
            "cold load into the cache",
            [load_ms],
            f"{graph.node_count:,} nodes, {graph.edge_count:,} edges, "
            f"~{graph.estimated_bytes / 2**20:.1f} MB estimated",
        )
    )

    trip_ms, loaded = timed(partial(load_graph, snapshot_id, engine=engine))
    digest = loaded.digest()
    same = digest == stats.get("graph_digest")
    rows.append(
        summary(
            "round trip: load_graph",
            [trip_ms],
            f"digest {digest[:16]} {'matches' if same else 'DIFFERS from'} the saved one",
        )
    )
    del loaded  # tens of thousands of objects the garbage collector would keep walking
    gc.collect()

    symbols = [n for n in range(graph.node_count) if graph.kind_of(n) in SYMBOLS]
    calls = [EdgeKind.CALLS]
    busiest = sorted(symbols, key=lambda n: (-len(graph.in_edges(n, calls)), graph.keys[n]))
    sampled = rng.sample(symbols, min(args.samples, len(symbols)))
    targets = {"most-called symbols": busiest[: args.samples], "random symbols": sampled}
    for label, nodes in targets.items():
        times: list[float] = []
        reached: list[int] = []
        truncated = 0
        for node in nodes:
            call = partial(store.callers, snapshot_id, graph.keys[node], 3)
            result = call()
            reached.append(len(result.nodes))
            truncated += result.truncated
            times += [timed(call)[0] for _ in range(rounds)]
        rows.append(
            summary(
                f"callers depth 3, {label}",
                times,
                f"median {statistics.median(reached):.0f} nodes reached, max {max(reached):,}"
                + (f"; {truncated} hit the 1,000-node cap" if truncated else ""),
            )
        )

    root = graph.index["dir:."]
    dirs = [n for n in range(graph.node_count) if graph.kind_of(n) == NodeKind.DIR]
    files = [n for n in range(graph.node_count) if graph.kind_of(n) == NodeKind.FILE]
    biggest_dirs = sorted(dirs, key=lambda n: (-len(graph.descendants(n)), graph.keys[n]))
    biggest_files = sorted(files, key=lambda n: (-len(graph.descendants(n)), graph.keys[n]))
    cases: list[tuple[str, list[int], Level]] = [
        ("root, dir level", [root], "dir"),
        ("root, file level", [root], "file"),
        ("largest directories, dir level", biggest_dirs[1 : args.samples + 1], "dir"),
        ("largest files, symbol level", biggest_files[: args.samples], "symbol"),
    ]
    for label, parents, level in cases:
        first: list[float] = []
        repeat: list[float] = []
        sizes: list[tuple[int, int]] = []
        for parent in parents:
            call_subgraph = partial(store.subgraph, snapshot_id, graph.keys[parent], level)
            took, sub = timed(call_subgraph)
            first.append(took)
            sizes.append((len(sub.nodes), len(sub.edges)))
            repeat += [timed(call_subgraph)[0] for _ in range(rounds)]
        children = max(size[0] for size in sizes)
        links = max(size[1] for size in sizes)
        note = f"up to {children:,} children and {links:,} aggregated edges"
        rows.append(summary(f"subgraph, {label} (first call)", first, note))
        rows.append(summary(f"subgraph, {label} (memoized)", repeat))

    picked = [store.get_node(snapshot_id, graph.keys[node]) for node in sampled]
    names = [found.name for found in picked if found is not None and found.name.strip()]
    spans = [
        (found.path, found.span.start_line, found.span.end_line)
        for found in picked
        if found is not None and found.span is not None
    ]
    reads: list[tuple[str, list[Callable[[], object]], str]] = [
        (
            "find_symbols",
            [partial(store.find_symbols, snapshot_id, name) for name in names],
            "names of the random symbols",
        ),
        ("get_node", [partial(store.get_node, snapshot_id, graph.keys[n]) for n in sampled], ""),
        (
            "locate",
            [partial(store.locate, snapshot_id, path, start) for path, start, _ in spans],
            "first line of each random symbol",
        ),
        (
            "read_code",
            [partial(store.read_code, snapshot_id, path, a, b) for path, a, b in spans],
            "each random symbol's span",
        ),
    ]
    for label, calls_to_time, note in reads:
        rows.append(
            summary(label, [ms for call in calls_to_time for ms in repeated(call, rounds)], note)
        )
    engine.dispose()

    print(f"Snapshot {snapshot_id}: {info.repo.owner}/{info.repo.name} @ {info.commit_sha[:12]}")
    print()
    print("| Operation | Calls | p50 | p95 | Max | Notes |")
    print("|---|---|---|---|---|---|")
    for row in rows:
        print(
            f"| {row['operation']} | {row['calls']} | {row['p50_ms']:.1f} ms | "
            f"{row['p95_ms']:.1f} ms | {row['max_ms']:.1f} ms | {row['note']} |"
        )
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    if not same:
        raise SystemExit("The round trip failed: the stored graph has a different digest.")


if __name__ == "__main__":
    main()
