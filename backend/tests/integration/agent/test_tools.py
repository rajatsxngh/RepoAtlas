"""Each tool on the built ``py_small`` snapshot (AGT-1, AGT-2): real store, retriever, and
impact engine; no model."""

from __future__ import annotations

import pytest

from repoatlas.agent.tools import MAX_READ_LINES, ToolError
from repoatlas.answers.validate import StoreBlobs
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.store.blobs import split_lines
from tests.integration.agent.conftest import Built

CART = "src/shop/cart.py"
TOTAL = "py:src/shop/cart.py:Cart.total:method"
ADD = "py:src/shop/cart.py:Cart.add:method"
DESCRIBE = "py:src/shop/cart.py:Cart.describe:method"
DISCOUNT = "py:src/shop/cart.py:Cart._discount:method"
ROUND = "py:src/shop/utils/helpers.py:round_money:function"
FORMAT = "py:src/shop/utils/helpers.py:format_price:function"
GET_CART = "py:src/shop/app.py:get_cart:function"


def lines_of(shop: Built, ref: EvidenceRef) -> str:
    """The text a reference points at, read from the stored blob."""
    blob = StoreBlobs(shop.engine).text(ref.blob_oid)
    assert blob is not None, ref
    lines = split_lines(blob)
    assert ref.end_line <= len(lines), ref
    return "\n".join(lines[ref.start_line - 1 : ref.end_line])


def test_every_tool_returns_evidence_that_exists_in_the_stored_blobs(shop: Built):
    box = shop.toolbox()
    results = [
        box.call("search_code", {"query": "cart total"}),
        box.call("find_definition", {"name": "Cart"}),
        box.call("find_callers", {"symbol_key": ROUND, "depth": 2}),
        box.call("find_callees", {"symbol_key": DESCRIBE}),
        box.call("trace_path", {"from_key": GET_CART, "to_key": FORMAT}),
        box.call("read_file", {"path": CART, "start": 1, "end": 30}),
        box.call("get_owners", {"path": CART}),
        box.call("get_impact", {"symbol_key": ROUND}),
    ]
    for result in results:
        assert result.evidence, result.content
        assert len(set(result.evidence)) == len(result.evidence)  # no reference twice
        for ref in result.evidence:
            assert len(ref.blob_oid) == 40 and lines_of(shop, ref)


def test_search_code_returns_ranked_code_with_line_numbers(shop: Built):
    result = shop.toolbox().call("search_code", {"query": "cart total price", "k": 3})
    assert result.content.startswith("Results, best first: 3.")
    assert len(result.evidence) == 3
    best = result.evidence[0]
    assert (best.path, best.symbol_key) == (CART, TOTAL)
    assert "[1] src/shop/cart.py lines 17-19" in result.content
    assert "18 |     def total(self) -> Decimal:" in result.content
    one = shop.toolbox().call("search_code", {"query": "cart total price", "k": 1})
    assert one.evidence == [best]


def test_find_definition_lists_symbols_with_keys_and_locations(shop: Built):
    result = shop.toolbox().call("find_definition", {"name": "total"})
    assert f"- {TOTAL} (method Cart.total, {CART} lines 17-19); exact match" in result.content
    [ref] = result.evidence
    assert (ref.path, ref.start_line, ref.end_line, ref.symbol_key) == (CART, 17, 19, TOTAL)
    assert "def total" in lines_of(shop, ref)
    nothing = shop.toolbox().call("find_definition", {"name": "zzz_no_such_thing_qq"})
    assert nothing.content.startswith("No symbol") and nothing.evidence == []


def test_find_callers_gives_confidence_and_call_sites(shop: Built):
    direct = shop.toolbox().call("find_callers", {"symbol_key": ROUND})
    assert f"- depth 1: {ADD} " in direct.content
    edge = f"- {ADD} -[calls, confidence 0.94, name_match]-> {ROUND}, seen at {CART} line 15"
    assert edge in direct.content
    site = next(ref for ref in direct.evidence if ref.path == CART)
    assert site.symbol_key == ADD and "round_money(price)" in lines_of(shop, site)
    deeper = shop.toolbox().call("find_callers", {"symbol_key": ROUND, "depth": 2})
    assert "- depth 2: py:src/shop/app.py:add_item:function" in deeper.content
    assert len(deeper.evidence) > len(direct.evidence)


def test_find_callees_follows_calls_outward(shop: Built):
    result = shop.toolbox().call("find_callees", {"symbol_key": DESCRIBE})
    assert f"- depth 1: {FORMAT} " in result.content and DISCOUNT in result.content
    # Both calls are on line 25: one reference, not two.
    assert [(ref.path, ref.start_line) for ref in result.evidence] == [(CART, 25)]
    leaf = shop.toolbox().call("find_callees", {"symbol_key": DISCOUNT})
    assert leaf.content.startswith("Nothing called from") and leaf.evidence == []


def test_trace_path_lists_the_symbols_and_edges_between_two_symbols(shop: Built):
    result = shop.toolbox().call("trace_path", {"from_key": GET_CART, "to_key": FORMAT})
    assert result.content.startswith("A shortest path of 2 steps, following the edges' direction.")
    order = [result.content.index(key) for key in (GET_CART, DESCRIBE, FORMAT)]
    assert order == sorted(order)
    paths = {ref.path for ref in result.evidence}
    assert paths == {"src/shop/app.py", CART, "src/shop/utils/helpers.py"}
    same = shop.toolbox().call("trace_path", {"from_key": FORMAT, "to_key": FORMAT})
    assert same.content == "Both keys name the same symbol."


def test_read_file_returns_numbered_lines_and_one_reference(shop: Built):
    result = shop.toolbox().call("read_file", {"path": CART, "start": 14, "end": 15})
    assert result.content == (
        f"{CART} lines 14-15 (the file has 30 lines).\n"
        "14 |     def add(self, name: str, price: Decimal) -> None:\n"
        "15 |         self.items.append((name, round_money(price)))"
    )
    [ref] = result.evidence
    assert (ref.start_line, ref.end_line) == (14, 15) and not result.truncated
    past = shop.toolbox().call("read_file", {"path": CART, "start": 20, "end": 9_999})
    assert past.evidence[0].end_line == 30 and not past.truncated  # the whole rest fits
    with pytest.raises(ToolError, match="past the end"):
        shop.toolbox().call("read_file", {"path": CART, "start": 31, "end": 40})


def test_read_file_returns_at_most_the_line_cap(shop: Built, monkeypatch: pytest.MonkeyPatch):
    assert MAX_READ_LINES == 200
    monkeypatch.setattr("repoatlas.agent.tools.MAX_READ_LINES", 10)
    result = shop.toolbox().call("read_file", {"path": CART, "start": 1, "end": 30})
    assert result.truncated and result.evidence[0].end_line == 10
    assert "read on from line 11" in result.content and "11 | " not in result.content


def test_get_owners_takes_a_path_or_a_symbol(shop: Built):
    by_path = shop.toolbox().call("get_owners", {"path": CART})
    assert "- RepoAtlas Fixtures: 100% (1 commits, last on 2026-01-01)" in by_path.content
    by_symbol = shop.toolbox().call("get_owners", {"symbol_key": TOTAL})
    assert by_symbol == by_path
    assert [(ref.path, ref.start_line) for ref in by_path.evidence] == [(CART, 1)]


def test_get_impact_groups_what_a_change_reaches(shop: Built):
    result = shop.toolbox().call("get_impact", {"symbol_key": ROUND})
    text = result.content
    assert text.index("Tests (") < text.index("Callers (")
    assert f"- {ADD} ({CART}): score " in text and f"via {ROUND} -> {ADD}" in text
    assert "file:tests/test_cart.py" in text
    few = shop.toolbox().call("get_impact", {"symbol_key": ROUND, "limit": 2})
    assert few.truncated and len(few.evidence) <= 2


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../secrets.env", "src/shop/../../x", "C:\\Windows\\win.ini", "src/nope.py"],
)
def test_reads_outside_the_snapshot_are_refused_as_not_allowed(shop: Built, path: str):
    box = shop.toolbox()
    with pytest.raises(ToolError) as error:
        box.call("read_file", {"path": path, "start": 1, "end": 5})
    assert error.value.allowed is False
    with pytest.raises(ToolError) as error:
        box.call("get_owners", {"path": path})
    assert error.value.allowed is False


def test_unknown_symbols_and_other_snapshots_are_errors_the_caller_can_act_on(shop: Built):
    box = shop.toolbox()
    for name, arguments in (
        ("find_callers", {"symbol_key": "py:src/shop/cart.py:Cart.nope:method"}),
        ("find_callees", {"symbol_key": "py:src/shop/cart.py:total:function"}),
        ("trace_path", {"from_key": TOTAL, "to_key": "py:x.py:y:function"}),
        ("get_owners", {"symbol_key": "py:x.py:y:function"}),
        ("get_impact", {"symbol_key": "py:x.py:y:function"}),
    ):
        with pytest.raises(ToolError, match="no symbol with the key") as error:
            box.call(name, arguments)
        assert error.value.allowed and "find_definition" in str(error.value)
    # A toolbox is bound to its snapshot: one for a snapshot that doesn't exist reads nothing.
    other = shop.toolbox()
    other.snapshot_id = shop.snapshot_id + 999
    with pytest.raises(ToolError, match="does not exist"):
        other.call("find_definition", {"name": "Cart"})
