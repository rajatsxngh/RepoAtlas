"""Rerank with the exact-name bonus, and packing evidence into a token budget (RET-6)."""

from __future__ import annotations

import math

from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.rerank import (
    NAME_BONUS,
    Candidate,
    QueryNames,
    pack,
    query_names,
    rerank,
)

NONE = QueryNames()

OID = "a" * 40


def candidate(
    chunk_id: int,
    *,
    name: str = "f",
    kind: str = "function",
    lines: tuple[int, int] = (1, 5),
    path: str = "a.py",
    tokens: int = 100,
    fused: float = 0.0,
    expanded: float = 0.0,
) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        node_key=f"py:{path}:{name}:{kind}",
        node_name=name.rsplit(".", 1)[-1],
        qualified_name=name,
        kind=kind,
        ref=EvidenceRef(blob_oid=OID, path=path, start_line=lines[0], end_line=lines[1]),
        tokens=tokens,
        fused=fused,
        expanded=expanded,
    )


def test_query_names_split_plain_and_dotted_identifiers() -> None:
    names = query_names("how does Cart.add round_money in 2 steps")
    assert names.plain == {"how", "does", "round_money", "in", "steps"}
    assert names.dotted == {"cart.add"}


def test_dotted_names_match_qualified_names_only() -> None:
    names = query_names("Cart.add and shop.cart.Cart")
    assert names.match("add", "Cart.add")
    assert not names.match("add", "GiftCart.add")
    assert names.match("Cart", "Cart")  # after the module path shop.cart
    assert not names.match("cart", "shop")  # a module path alone is not a symbol
    assert query_names("ROUND_MONEY").match("round_money", "round_money")


def test_the_final_score_is_the_better_of_fused_and_expanded() -> None:
    ranked = rerank(
        [candidate(1, fused=0.02, expanded=0.01), candidate(2, fused=0.0, expanded=0.03)],
        NONE,
    )
    assert [(item.candidate.chunk_id, item.score, item.bonus) for item in ranked] == [
        (2, 0.03, 0.0),
        (1, 0.02, 0.0),
    ]


def test_an_exact_name_match_gets_the_bonus() -> None:
    ranked = rerank(
        [candidate(1, name="other", fused=0.03), candidate(2, name="getUser", fused=0.02)],
        query_names("where is getuser defined"),
    )
    assert ranked[0].candidate.chunk_id == 2
    assert math.isclose(ranked[0].score, 0.02 + NAME_BONUS) and ranked[0].bonus == NAME_BONUS
    assert (
        rerank([candidate(2, name="getUser", fused=0.02)], query_names("getUser"), bonus=0)[0].bonus
        == 0.0
    )


def test_ties_break_by_chunk_id() -> None:
    ranked = rerank([candidate(9, fused=0.5), candidate(3, fused=0.5)], NONE)
    assert [item.candidate.chunk_id for item in ranked] == [3, 9]


def test_packing_takes_chunks_in_order_within_the_budget() -> None:
    ranked = rerank(
        [
            candidate(1, fused=0.9, tokens=600, path="a.py"),
            candidate(2, fused=0.8, tokens=500, path="b.py"),  # does not fit after 1
            candidate(3, fused=0.7, tokens=300, path="c.py"),  # still fits
            candidate(4, fused=0.6, tokens=200, path="d.py"),  # over again
        ],
        NONE,
    )
    packed = pack(ranked, budget_tokens=1000)
    assert [item.candidate.chunk_id for item in packed.selected] == [1, 3]
    assert packed.tokens == 900
    assert [(item.candidate.chunk_id, reason) for item, reason in packed.skipped] == [
        (2, "budget"),
        (4, "budget"),
    ]


def test_chunks_inside_a_selected_function_are_skipped() -> None:
    outer = candidate(1, name="outer", lines=(10, 40), fused=0.9)
    inner = candidate(2, name="inner", lines=(12, 20), fused=0.8)
    elsewhere = candidate(3, name="inner", lines=(12, 20), path="b.py", fused=0.7)
    packed = pack(rerank([outer, inner, elsewhere], NONE))
    assert [item.candidate.chunk_id for item in packed.selected] == [1, 3]
    assert [(item.candidate.chunk_id, reason) for item, reason in packed.skipped] == [
        (2, "contained")
    ]


def test_class_and_module_chunks_never_stand_in_for_code_inside_them() -> None:
    summary = candidate(1, name="Cart", kind="class", lines=(1, 50), fused=0.9)
    module = candidate(2, name="a.py", kind="module", lines=(1, 60), fused=0.85)
    method = candidate(3, name="add", kind="method", lines=(5, 9), fused=0.8)
    packed = pack(rerank([summary, module, method], NONE))
    assert [item.candidate.chunk_id for item in packed.selected] == [1, 2, 3]


def test_a_limit_caps_the_number_of_chunks() -> None:
    ranked = rerank([candidate(i, fused=1 / i, path=f"{i}.py") for i in range(1, 6)], NONE)
    packed = pack(ranked, k=2)
    assert [item.candidate.chunk_id for item in packed.selected] == [1, 2]
    assert {reason for _, reason in packed.skipped} == {"limit"}
    assert pack([], budget_tokens=10).selected == []
