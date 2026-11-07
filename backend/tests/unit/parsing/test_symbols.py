from __future__ import annotations

import pytest

from repoatlas.parsing.symbols import (
    KeyAllocator,
    SymbolKeyParts,
    decode_part,
    encode_part,
    make_symbol_key,
    parse_symbol_key,
)


@pytest.mark.parametrize(
    "parts",
    [
        SymbolKeyParts("py", "src/app.py", "create_app", "function"),
        SymbolKeyParts("py", "src/app.py", "Router.add_route", "method"),
        SymbolKeyParts("ts", "src/cart.ts", "Cart", "class", ordinal=3),
        SymbolKeyParts("js", "lib/a b/ünï.js", "<anon>", "function", ordinal=2),
        SymbolKeyParts("ts", "src/a.test.ts", "Cart > adds: items ~ fast", "test"),
        SymbolKeyParts("py", "weird%3Apath.py", "x%y", "function"),
    ],
)
def test_keys_round_trip(parts: SymbolKeyParts) -> None:
    key = make_symbol_key(parts.lang, parts.path, parts.qualname, parts.kind, parts.ordinal)
    assert key.count(":") == 3
    assert parse_symbol_key(key) == parts


def test_key_format() -> None:
    assert make_symbol_key("py", "a.py", "f", "function") == "py:a.py:f:function"
    assert make_symbol_key("py", "a.py", "f", "function", 2) == "py:a.py:f:function~2"
    assert make_symbol_key("ts", "a.ts", "T > x:y", "test") == "ts:a.ts:T > x%3Ay:test"


def test_encoding_is_reversible_even_for_escape_lookalikes() -> None:
    for text in ["a:b~c%d", "%3A", "%25", "100%", "::~~%%"]:
        assert decode_part(encode_part(text)) == text
    assert encode_part("%3A") == "%253A"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "py:a.py:f",
        "py:a.py:f:function:extra",
        "rb:a.py:f:function",
        "py:a.py:f:widget",
        "py:a.py:f:function~1",
        "py:a.py:f:function~02",
        "py:a.py:f:function~x",
    ],
)
def test_malformed_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError):
        parse_symbol_key(key)


def test_ordinal_must_be_positive() -> None:
    with pytest.raises(ValueError):
        make_symbol_key("py", "a.py", "f", "function", 0)


def test_allocator_numbers_same_named_siblings_per_kind() -> None:
    keys = KeyAllocator("py", "m.py")
    assert keys.allocate("f", "function") == "py:m.py:f:function"
    assert keys.allocate("f", "function") == "py:m.py:f:function~2"
    assert keys.allocate("f", "class") == "py:m.py:f:class"
    assert keys.allocate("C.f", "method") == "py:m.py:C.f:method"
    assert keys.allocate("f", "function") == "py:m.py:f:function~3"
