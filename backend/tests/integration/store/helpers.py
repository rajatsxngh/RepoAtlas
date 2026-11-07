"""Shared helpers for store tests: fixture graphs with their inventories and blob bytes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repoatlas.graph.build import build_graph
from repoatlas.graph.model import CodeGraph, Owner
from repoatlas.ingest.github import RepoMeta, RepoRef
from repoatlas.ingest.inventory import Inventory
from repoatlas.ingest.source import DirectorySourceTree, MemorySourceTree, SourceTree, TreeEntry
from repoatlas.pipeline.graph import read_project_files
from repoatlas.pipeline.parse import parse_tree

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REPOS = FIXTURES / "repos"
GOLDEN = FIXTURES / "golden"
CAPS = {"max_source_files": 5_000, "max_file_bytes": 1_000_000}
SHA_A = "a" * 40
SHA_B = "b" * 40
FINGERPRINT = "f" * 64


@dataclass(frozen=True)
class Built:
    inventory: Inventory
    graph: CodeGraph
    blobs: dict[str, bytes]


def build(tree: SourceTree) -> Built:
    """Inventory, parse, and graph ``tree`` (no history), keeping the indexed files' bytes."""
    parsed = parse_tree(tree, **CAPS)
    project = read_project_files(tree, max_file_bytes=CAPS["max_file_bytes"])
    graph = build_graph(parsed.inventory, parsed.files, project=project)
    blobs = {
        entry.blob_oid: tree.read(TreeEntry(entry.path, entry.mode, entry.blob_oid, entry.size))
        for entry in parsed.inventory.indexed
    }
    return Built(inventory=parsed.inventory, graph=graph, blobs=blobs)


def build_fixture(name: str) -> Built:
    with DirectorySourceTree(REPOS / name) as tree:
        return build(tree)


def build_files(files: dict[str, bytes]) -> Built:
    with MemorySourceTree(dict(files)) as tree:
        return build(tree)


def fixture_files(name: str) -> dict[str, bytes]:
    root = REPOS / name
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def repo_meta(owner: str = "acme", name: str = "shop", branch: str = "main") -> RepoMeta:
    return RepoMeta(
        ref=RepoRef(owner=owner, name=name),
        default_branch=branch,
        private=False,
        size_kb=10,
        html_url=f"https://github.com/{owner}/{name}",
        archived=False,
        fork=False,
    )


def with_owners(graph: CodeGraph, owners: dict[str, list[Owner]]) -> CodeGraph:
    return CodeGraph(
        nodes=graph.nodes,
        edges=graph.edges,
        unresolved=graph.unresolved,
        owners=owners,
        stats=graph.stats,
    )
