"""Reciprocal rank fusion and seeds on synthetic rankings (RET-6)."""

from __future__ import annotations

import math

import pytest

from repoatlas.retrieval.fusion import RRF_K, SEEDS, reciprocal_rank_fusion, select_seeds


def rrf(*ranks: int) -> float:
    return sum(1.0 / (RRF_K + rank) for rank in ranks)


def test_scores_sum_over_the_lists_an_item_is_in() -> None:
    fused = reciprocal_rank_fusion({"vector": [1, 2, 3], "keyword": [3, 4]})
    by_id = {item.id: item for item in fused}
    assert math.isclose(by_id[3].score, rrf(3, 1))
    assert math.isclose(by_id[1].score, rrf(1))
    assert math.isclose(by_id[4].score, rrf(2))
    assert by_id[3].ranks == {"vector": 3, "keyword": 1}
    assert [item.id for item in fused] == [3, 1, 2, 4]


def test_agreement_beats_a_single_top_rank() -> None:
    fused = reciprocal_rank_fusion({"vector": [10, 20], "keyword": [30, 20]})
    assert fused[0].id == 20  # second in both beats first in one


def test_ties_break_by_best_rank_then_id() -> None:
    fused = reciprocal_rank_fusion({"vector": [5, 7], "keyword": [7, 5]})
    assert math.isclose(fused[0].score, fused[1].score)
    assert [item.id for item in fused] == [5, 7]  # equal best ranks: lower ID first
    fused = reciprocal_rank_fusion({"a": [9], "b": [], "c": [1, 2]})
    assert [item.id for item in fused] == [1, 9, 2]


def test_fusion_does_not_depend_on_the_order_of_sources() -> None:
    one = reciprocal_rank_fusion({"vector": [1, 2, 3], "keyword": [3, 2, 1]})
    two = reciprocal_rank_fusion({"keyword": [3, 2, 1], "vector": [1, 2, 3]})
    assert one == two


def test_duplicates_in_one_list_count_once_at_their_best_rank() -> None:
    [only] = reciprocal_rank_fusion({"vector": [4, 4, 4]})
    assert math.isclose(only.score, rrf(1)) and only.ranks == {"vector": 1}


def test_empty_rankings_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"vector": [], "keyword": []}) == []


def test_the_constant_controls_how_fast_ranks_decay() -> None:
    [first, second] = reciprocal_rank_fusion({"v": [1, 2]}, k=0)
    assert (first.score, second.score) == (1.0, 0.5)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"v": [1]}, k=-1)


def test_seeds_are_the_top_fused_results() -> None:
    fused = reciprocal_rank_fusion({"vector": list(range(1, 21)), "keyword": [15, 16]})
    seeds = select_seeds(fused)
    assert len(seeds) == SEEDS == 8
    assert [seed.id for seed in seeds][:2] == [15, 16]
    assert seeds == fused[:SEEDS]
    assert select_seeds(fused, count=0) == []
    assert select_seeds(fused[:3]) == fused[:3]
