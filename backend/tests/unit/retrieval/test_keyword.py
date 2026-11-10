"""Identifier splitting, the keyword document, and the query builder (RET-5)."""

from __future__ import annotations

import pytest

from repoatlas.retrieval.keyword import (
    MAX_QUERY_TERMS,
    build_query,
    identifiers,
    split_identifier,
    split_text,
    stem,
    tsvector_literal,
)


@pytest.mark.parametrize(
    ("identifier", "words"),
    [
        ("getUserById", ["get", "user", "by", "id"]),
        ("snake_case_name", ["snake", "case", "name"]),
        ("pkg.mod.func", ["pkg", "mod", "func"]),
        ("HTTPServer", ["http", "server"]),
        ("parseHTTPResponse", ["parse", "http", "response"]),
        ("XMLHttpRequest", ["xml", "http", "request"]),
        ("__init__", ["init"]),
        ("SCREAMING_CASE", ["screaming", "case"]),
        ("base64Encode", ["base", "64", "encode"]),
        ("$scope", ["scope"]),
        ("Cart", ["cart"]),
        ("x", ["x"]),
        ("v2", ["v", "2"]),
    ],
)
def test_split_identifier(identifier: str, words: list[str]) -> None:
    assert split_identifier(identifier) == words


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("self.items.append(x)", ["self.items.append", "x"]),
        ("from shop.cart import Cart", ["from", "shop.cart", "import", "Cart"]),
        ("a-b + c_d", ["a", "b", "c_d"]),
        ("price = 10.5", ["price", "10", "5"]),
    ],
)
def test_identifiers_keep_dotted_names_whole(text: str, found: list[str]) -> None:
    assert identifiers(text) == found


@pytest.mark.parametrize(
    ("word", "stemmed"),
    [
        ("routes", "route"),
        ("registered", "register"),
        ("adding", "add"),
        ("items", "item"),
        ("class", "class"),
        ("is", "is"),
        ("bus", "bus"),
        ("red", "red"),
    ],
)
def test_stem(word: str, stemmed: str) -> None:
    assert stem(word) == stemmed


def test_split_text_keeps_raw_identifiers_and_their_words() -> None:
    assert split_text("getUserById(user_id)") == ("getuserbyid get user by id user_id user id")
    assert split_text("registerRoutes") == "registerroutes register routes route"


def test_tsvector_literals_weight_header_terms_and_keep_positions() -> None:
    literal = tsvector_literal("a.py\nadd_item", "def add_item(cart): return cart")
    lexemes = dict(entry.split(":", 1) for entry in literal.split(" "))
    assert lexemes["'add_item'"] == "2A,4D"  # header position 2 (a.py is 1), body position 4
    assert lexemes["'item'"] == "2A,4D"
    assert lexemes["'cart'"] == "5D,7D"
    assert "'a.py'" in lexemes
    assert list(lexemes) == sorted(lexemes)


def test_tsvector_literals_escape_quotes_and_backslashes() -> None:
    assert tsvector_literal("", "") == ""
    # identifiers never hold quotes, but the literal stays safe if a term ever does
    from repoatlas.retrieval import keyword

    assert keyword._literal("it's\\") == "'it''s\\\\'"


@pytest.mark.parametrize(
    ("question", "query"),
    [
        ("where are routes registered?", "route or register"),
        ("getUserById", "getuserbyid or get or user or id"),
        ("how does Cart.add round prices", "cart or add or round or price"),
        ("snake_case_name", "snake or case or name"),
        ("what is the", None),
        ("", None),
        ("'; DROP TABLE chunks; --", "drop or table or chunk"),
    ],
)
def test_build_query(question: str, query: str | None) -> None:
    assert build_query(question) == query


def test_queries_are_capped() -> None:
    query = build_query(" ".join(f"word{index}x" for index in range(100)))
    assert query is not None and len(query.split(" or ")) == MAX_QUERY_TERMS
