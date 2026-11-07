"""Record what the API's in-memory graph answers for the mock fixtures, so a unit test can check
that mock mode aggregates, traverses, and finds paths exactly like the backend.

Run from frontend/ after the golden graphs change (it uses the backend's environment):

    uv run --project ../backend python scripts/export-mock-expectations.py
"""

from __future__ import annotations

import json
from pathlib import Path

from repoatlas.graph.model import CodeGraph, EdgeKind
from repoatlas.store.cache import SnapshotGraph

FRONTEND = Path(__file__).resolve().parents[1]
GOLDEN = FRONTEND.parent / "backend" / "tests" / "fixtures" / "golden"
OUT = FRONTEND / "src" / "lib" / "api" / "mock" / "fixtures" / "data"
# The API's default for /path (GraphStore.shortest_path); the cache method itself follows all kinds.
PATH_KINDS = [EdgeKind.CALLS, EdgeKind.IMPORTS]


def export(name: str) -> None:
    data = json.loads((GOLDEN / f"{name}.graph.json").read_text("utf-8"))
    graph = CodeGraph.model_validate(data["graph"])
    snapshot = SnapshotGraph.from_code_graph(graph)
    keys = snapshot.keys

    subgraphs = []
    for key, node in graph.nodes.items():
        levels = ["dir", "file"] if node.kind == "dir" else ["symbol"]
        for level in levels:
            found = snapshot.aggregate(snapshot.index[key], level)  # type: ignore[arg-type]
            subgraphs.append(
                {
                    "parent": key,
                    "level": level,
                    "nodes": [keys[child] for child in found.children],
                    "edges": [
                        {
                            "src": keys[link.src],
                            "dst": keys[link.dst],
                            "kind": link.kind.value,
                            "count": link.count,
                            "min_confidence": link.min_confidence,
                            "max_confidence": link.max_confidence,
                        }
                        for link in found.links
                    ],
                }
            )

    neighbors = []
    symbols = [key for key, node in graph.nodes.items() if node.kind.value not in ("dir",)]
    for key in symbols:
        for direction in ("in", "out", "both"):
            for kinds in (None, [EdgeKind.CALLS]):
                reached = snapshot.traverse(
                    snapshot.index[key], direction=direction, kinds=kinds, depth=3
                )
                neighbors.append(
                    {
                        "key": key,
                        "direction": direction,
                        "kinds": None if kinds is None else [kind.value for kind in kinds],
                        "depth": 3,
                        "nodes": sorted(
                            ([keys[n], level] for n, level in reached.depths.items() if level),
                            key=lambda item: (item[1], item[0]),
                        ),
                        "edge_count": len(reached.edges),
                    }
                )

    paths = []
    callables = [
        key
        for key, node in graph.nodes.items()
        if node.kind.value in ("function", "method", "test", "route")
    ]
    for src in callables:
        for dst in callables:
            if src == dst:
                continue
            found = snapshot.shortest_path(snapshot.index[src], snapshot.index[dst], PATH_KINDS)
            paths.append(
                {
                    "from": src,
                    "to": dst,
                    "length": None if found is None else len(found.nodes),
                    "directed": None if found is None else found.directed,
                }
            )

    out = {"name": name, "subgraphs": subgraphs, "neighbors": neighbors, "paths": paths}
    path = OUT / f"{name}.expected.json"
    path.write_text(json.dumps(out) + "\n", "utf-8", newline="\n")
    print(f"{name}: {len(subgraphs)} subgraphs, {len(neighbors)} traversals, {len(paths)} paths")


if __name__ == "__main__":
    for fixture in ("py_small", "ts_small"):
        export(fixture)
