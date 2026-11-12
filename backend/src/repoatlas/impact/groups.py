"""Groups and ranking (IMP-3).

- **Tests:** ``test`` nodes and test files.
- **Public APIs:** ``route`` nodes and nodes marked ``meta.public``.
- **Callers:** everything else.

The combined ranking orders items by score, then best single-path score, then depth (nearer
first), then key; each group keeps that order.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from repoatlas.graph.model import Node, NodeKind
from repoatlas.impact.schema import GROUPS, ImpactGroupName


class Rankable(Protocol):
    @property
    def symbol_key(self) -> str: ...
    @property
    def score(self) -> float: ...
    @property
    def best_score(self) -> float: ...
    @property
    def depth(self) -> int: ...


def group_of(node: Node) -> ImpactGroupName:
    if node.kind == NodeKind.TEST or (node.kind == NodeKind.FILE and node.meta.get("is_test")):
        return "test"
    if node.kind == NodeKind.ROUTE or node.meta.get("public") is True:
        return "public_api"
    return "caller"


def rank_key(item: Rankable) -> tuple[float, float, int, str]:
    return (-item.score, -item.best_score, item.depth, item.symbol_key)


def ranked[T: Rankable](items: Iterable[T]) -> list[T]:
    return sorted(items, key=rank_key)


def count_by_group(groups: Iterable[ImpactGroupName]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(GROUPS, 0)
    for group in groups:
        counts[group] += 1
    return counts
