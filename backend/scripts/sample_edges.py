"""Sample edges of a pinned repository's graph for a hand check (M02 T11). Not part of CI.

Usage (from backend/):
    uv run python scripts/sample_edges.py [--seed 2] [--structural 20] [--per-tier 5]

Builds the graph of the pinned commit, then draws a seeded sample: ``--structural`` edges split
evenly across ``contains``, ``imports``, and ``inherits``, and ``--per-tier`` ``calls`` edges for
each tier. Each sampled edge is printed with the source text that supports it and links to the
lines on GitHub, so it can be checked by hand. Needs network access; set REPOATLAS_GITHUB_TOKEN
if anonymous GitHub API calls fail.
"""

from __future__ import annotations

import argparse
import random
import time
from collections import Counter

from repoatlas.config import get_settings
from repoatlas.graph.build import GraphSettings
from repoatlas.graph.model import CodeGraph, Edge, EdgeKind
from repoatlas.graph.owners import read_commit_log
from repoatlas.graph.tiers import Tier
from repoatlas.ingest.fetch import Workspace, fetch_history, fetch_source
from repoatlas.ingest.github import parse_repo_url
from repoatlas.ingest.source import GitSourceTree, TreeEntry
from repoatlas.parsing.models import Range
from repoatlas.pipeline.graph import graph_tree

REPO = "https://github.com/colinhacks/zod"
COMMIT = "19c40d2072288b4cafec851e9d056a72a88e5973"
STRUCTURAL = (EdgeKind.CONTAINS, EdgeKind.IMPORTS, EdgeKind.INHERITS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--commit", default=COMMIT)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--structural", type=int, default=20)
    parser.add_argument("--per-tier", type=int, default=5)
    args = parser.parse_args()

    settings = get_settings()
    ref = parse_repo_url(args.repo)
    started = time.perf_counter()
    with Workspace(settings.data_dir / "work") as workspace:
        store = fetch_history(
            ref.clone_url, args.commit, workspace.path / "history.git", depth=settings.history_depth
        )
        history = read_commit_log(store, settings.owner_commits)
        label = f"github.com/{ref.full_name}"
        with fetch_source(
            ref.clone_url, args.commit, workspace.path / "source.git", label=label
        ) as tree:
            output = graph_tree(
                tree,
                max_source_files=settings.max_source_files,
                max_file_bytes=settings.max_file_bytes,
                history=history,
                settings=GraphSettings.from_settings(settings),
            )
            elapsed = time.perf_counter() - started
            graph = output.graph
            stats = graph.stats
            size = f"{len(graph.nodes):,} nodes, {len(graph.edges):,} edges"
            print(f"{label} @ {args.commit[:12]}: {size}")
            print(f"  nodes {stats.nodes_by_kind}")
            print(f"  edges {stats.edges_by_kind}")
            print(f"  call sites {stats.call_sites:,}: tiers {stats.call_sites_by_tier}")
            print(f"  unresolved {stats.unresolved_by_reason}; external {stats.external_calls:,}")
            print(f"  builtin {stats.builtin_calls:,}; imports {stats.imports_by_outcome}")
            print(
                f"  owners {stats.files_with_owners:,} files from {stats.history_commits:,} commits"
            )
            print(f"  digest {output.digest}; {elapsed:.1f} s including fetches")

            rng = random.Random(args.seed)
            share, extra = divmod(args.structural, len(STRUCTURAL))
            print("\n## Structural edges")
            number = 0
            for index, kind in enumerate(STRUCTURAL):
                pool = graph.edges_of(kind)
                count = min(len(pool), share + (1 if index < extra else 0))
                for edge in rng.sample(pool, count):
                    number += 1
                    show(number, edge, graph, tree, ref.full_name, args.commit)
            print("\n## Calls by tier")
            calls = graph.edges_of(EdgeKind.CALLS)
            by_tier = Counter(str(edge.meta.get("tier")) for edge in calls)
            print(f"calls edges by tier: {dict(sorted(by_tier.items()))}")
            number = 0
            for tier in Tier:
                pool = [edge for edge in calls if edge.meta.get("tier") == tier.value]
                for edge in rng.sample(pool, min(len(pool), args.per_tier)):
                    number += 1
                    show(number, edge, graph, tree, ref.full_name, args.commit)


def show(
    number: int, edge: Edge, graph: CodeGraph, tree: GitSourceTree, repo: str, commit: str
) -> None:
    src, dst = graph.nodes[edge.src], graph.nodes[edge.dst]
    tier = f" {edge.meta['tier']}" if "tier" in edge.meta else ""
    print(f"\n[{number}] {edge.kind.value}{tier} ({edge.provenance.value}, {edge.confidence})")
    print(f"  src {src.key}")
    print(f"  dst {dst.key}")
    if edge.site is not None:
        evidence = dst if edge.kind == EdgeKind.TESTED_BY else src
        print(f"  site {link(repo, commit, evidence.path, edge.site)}")
        print("    " + excerpt(tree, evidence.path, edge.site).replace("\n", "\n    "))
    for role, node in (("src", src), ("dst", dst)):
        where = node.name_range or node.span
        if where is not None:
            print(f"  {role} at {link(repo, commit, node.path, where)}")
            print("    " + line_of(tree, node.path, where.start_line))


def link(repo: str, commit: str, path: str, where: Range) -> str:
    lines = f"L{where.start_line}" + (
        f"-L{where.end_line}" if where.end_line != where.start_line else ""
    )
    return f"https://github.com/{repo}/blob/{commit}/{path}#{lines}"


def _lines(tree: GitSourceTree, path: str) -> list[str]:
    entry = next(e for e in tree.entries() if e.path == path)
    data = tree.read(TreeEntry(entry.path, entry.mode, entry.oid, entry.size))
    return data.decode("utf-8", "replace").split("\n")


def line_of(tree: GitSourceTree, path: str, line: int) -> str:
    return _lines(tree, path)[line - 1].strip()[:160]


def excerpt(tree: GitSourceTree, path: str, site: Range) -> str:
    lines = _lines(tree, path)[site.start_line - 1 : min(site.end_line, site.start_line + 2)]
    return "\n".join(line.rstrip()[:160] for line in lines)


if __name__ == "__main__":
    main()
