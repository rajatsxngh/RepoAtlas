"""Impact data read from a stored snapshot through ``GraphStore``."""

from __future__ import annotations

from collections.abc import Sequence

from repoatlas.graph.model import Edge, Node, UnresolvedCall
from repoatlas.impact.data import TextSite, literal_sites_in
from repoatlas.store.cache import SnapshotGraph
from repoatlas.store.graphstore import GraphStore


class StoreImpactData:
    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def graph(self, snapshot_id: int) -> SnapshotGraph:
        return self.store.graph(snapshot_id)

    def nodes(self, snapshot_id: int, keys: Sequence[str]) -> dict[str, Node]:
        return self.store.nodes_by_key(snapshot_id, keys)

    def edges(self, snapshot_id: int, positions: Sequence[int]) -> dict[int, Edge]:
        return self.store.edges_at(snapshot_id, positions)

    def blob_oids(self, snapshot_id: int, paths: Sequence[str]) -> dict[str, str]:
        return self.store.blob_oids(snapshot_id, paths)

    def unresolved_calls(self, snapshot_id: int, name: str) -> list[UnresolvedCall]:
        return self.store.unresolved_calls(snapshot_id, name)

    def literal_sites(self, snapshot_id: int, name: str, limit: int) -> list[TextSite]:
        found: list[TextSite] = []
        for path, oid, text in self.store.texts_containing(snapshot_id, name):
            found.extend(literal_sites_in(path, oid, text, name))
            if len(found) >= limit:
                break
        return found[:limit]

    def locate(self, snapshot_id: int, path: str, line: int) -> Node:
        return self.store.locate(snapshot_id, path, line)
