"""Keyword search over stored chunks (RET-5)."""

from __future__ import annotations

from sqlalchemy import Engine, text

from repoatlas.retrieval.keyword import keyword_search
from tests.integration.retrieval.conftest import Stored


def keys(engine: Engine, chunk_ids: list[int]) -> list[str]:
    with engine.connect() as connection:
        found: dict[int, str] = dict(
            connection.execute(
                text(
                    "SELECT c.id, n.symbol_key FROM chunks c JOIN nodes n ON n.id = c.node_id "
                    "WHERE c.id = ANY(:ids)"
                ),
                {"ids": chunk_ids},
            ).all()
        )
    return [found[chunk_id] for chunk_id in chunk_ids]


def search(stored: Stored, engine: Engine, question: str, *, ts: bool = False) -> list[str]:
    hits = keyword_search(stored.ts if ts else stored.py, question, engine=engine)
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
    return keys(engine, [hit.chunk_id for hit in hits])


def test_identifiers_find_their_symbols(stored: Stored, pg_engine: Engine) -> None:
    assert search(stored, pg_engine, "round_money")[0] == (
        "py:src/shop/utils/helpers.py:round_money:function"
    )
    assert search(stored, pg_engine, "formatPrice", ts=True)[0] == (
        "ts:src/cart.ts:formatPrice:function"
    )
    assert search(stored, pg_engine, "createServer", ts=True)[0] == (
        "ts:src/server.ts:createServer:function"
    )


def test_split_words_match_camel_and_snake_case(stored: Stored, pg_engine: Engine) -> None:
    found = search(stored, pg_engine, "format the price")
    assert "py:src/shop/utils/helpers.py:format_price:function" in found[:3]
    found = search(stored, pg_engine, "format price", ts=True)
    assert found[0] == "ts:src/cart.ts:formatPrice:function"


def test_plural_and_tense_words_match(stored: Stored, pg_engine: Engine) -> None:
    found = search(stored, pg_engine, "which carts get items added")
    assert "py:src/shop/app.py:add_item:function" in found[:3]


def test_header_matches_rank_above_body_matches(stored: Stored, pg_engine: Engine) -> None:
    found = search(stored, pg_engine, "describe")
    assert found[0] == "py:src/shop/cart.py:Cart.describe:method"


def test_nothing_to_search_for_or_no_match_gives_nothing(stored: Stored, pg_engine: Engine) -> None:
    assert keyword_search(stored.py, "what is the", engine=pg_engine) == []
    assert keyword_search(stored.py, "zebra quantum", engine=pg_engine) == []
    assert keyword_search(stored.py, "cart", k=0, engine=pg_engine) == []
    assert len(keyword_search(stored.py, "cart", k=2, engine=pg_engine)) == 2


def test_keyword_search_stays_within_its_snapshot(stored: Stored, pg_engine: Engine) -> None:
    py_ids = {hit.chunk_id for hit in keyword_search(stored.py, "cart", engine=pg_engine)}
    ts_ids = {hit.chunk_id for hit in keyword_search(stored.ts, "cart", engine=pg_engine)}
    assert py_ids and ts_ids and not py_ids & ts_ids
    with pg_engine.connect() as connection:
        snapshots = connection.execute(
            text("SELECT DISTINCT snapshot_id FROM chunks WHERE id = ANY(:ids)"),
            {"ids": list(py_ids)},
        ).scalars()
        assert list(snapshots) == [stored.py]
