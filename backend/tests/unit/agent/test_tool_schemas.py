"""The tools' definitions and argument checks (AGT-1): no store is touched."""

from __future__ import annotations

from typing import Any, cast

import pytest

from repoatlas.agent.tools import (
    MAX_READ_LINES,
    TOOL_NAMES,
    TOOLS,
    ReadFileArgs,
    Toolbox,
    ToolError,
    numbered,
    tool_definitions,
)
from repoatlas.llm.compat import build_request

PLANNED = (
    "search_code",
    "find_definition",
    "find_callers",
    "find_callees",
    "trace_path",
    "read_file",
    "get_owners",
    "get_impact",
)
KEY = "py:src/shop/cart.py:Cart.total:method"


def box() -> Toolbox:
    """A toolbox with nothing behind it: ``parse`` runs before any read."""
    nothing = cast(Any, None)
    return Toolbox(1, nothing, nothing, nothing)


def test_the_eight_planned_tools_are_defined_in_a_fixed_order():
    assert TOOL_NAMES == PLANNED
    assert [definition["name"] for definition in tool_definitions()] == list(PLANNED)
    assert tool_definitions() == tool_definitions()  # stable: it is part of the cached prefix


@pytest.mark.parametrize("spec", TOOLS, ids=lambda spec: spec.name)
def test_every_schema_is_flat_closed_and_described(spec):
    schema = spec.input_schema
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])
    for name, prop in schema["properties"].items():
        assert prop["type"] in ("string", "integer"), name  # flat: no nesting, no refs
        assert len(prop["description"]) > 10
    assert len(spec.description) > 80  # says what it returns and when to call it
    # The schema names exactly the model's fields, and what the model requires.
    fields = spec.args.model_fields
    assert set(schema["properties"]) == set(fields)
    assert set(schema["required"]) == {n for n, f in fields.items() if f.is_required()}


def test_the_definitions_are_accepted_by_the_request_builder():
    request = build_request(
        "claude-haiku-5-5",
        [{"role": "user", "content": "hi"}],
        max_tokens=256,
        tools=tool_definitions(),
        tool_choice="auto",
    )
    assert [tool["name"] for tool in request["tools"]] == list(PLANNED)
    assert request["tool_choice"] == {"type": "auto"}
    # No tool can fetch a URL, run a command, or pick another snapshot.
    properties = {name for tool in request["tools"] for name in tool["input_schema"]["properties"]}
    assert not properties & {"url", "command", "snapshot", "snapshot_id", "code"}


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("search_code", {"query": "cart"}, {"query": "cart", "k": 5}),
        ("search_code", {"query": "cart", "k": 10}, {"query": "cart", "k": 10}),
        ("find_definition", {"name": "Cart.total"}, {"name": "Cart.total"}),
        ("find_callers", {"symbol_key": KEY}, {"symbol_key": KEY, "depth": 1}),
        ("find_callees", {"symbol_key": KEY, "depth": 3}, {"symbol_key": KEY, "depth": 3}),
        ("trace_path", {"from_key": KEY, "to_key": KEY}, {"from_key": KEY, "to_key": KEY}),
        (
            "read_file",
            {"path": "a.py", "start": 1, "end": 9},
            {"path": "a.py", "start": 1, "end": 9},
        ),
        ("get_owners", {"path": "a.py"}, {"path": "a.py", "symbol_key": None}),
        ("get_owners", {"symbol_key": KEY}, {"path": None, "symbol_key": KEY}),
        ("get_impact", {"symbol_key": KEY}, {"symbol_key": KEY, "limit": 15}),
    ],
)
def test_valid_arguments_are_parsed_with_their_defaults(name, arguments, expected):
    spec, args = box().parse(name, arguments)
    assert spec.name == name and args.model_dump() == expected


@pytest.mark.parametrize(
    ("name", "arguments", "problem"),
    [
        ("search_code", {}, "query"),
        ("search_code", {"query": ""}, "query"),
        ("search_code", {"query": "x" * 501}, "query"),
        ("search_code", {"query": "cart", "k": 11}, "k"),
        ("search_code", {"query": "cart", "k": 0}, "k"),
        ("search_code", {"query": "cart", "k": "3"}, "k"),  # strict: a string is not a number
        ("search_code", {"query": "cart", "snapshot": 2}, "snapshot"),  # closed: no extra fields
        ("find_callers", {"symbol_key": KEY, "depth": 4}, "depth"),
        ("find_callees", {"symbol_key": KEY, "depth": True}, "depth"),
        ("trace_path", {"from_key": KEY}, "to_key"),
        ("read_file", {"path": "a.py", "start": 0, "end": 5}, "start"),
        ("read_file", {"path": "a.py", "start": 9, "end": 3}, "before start"),
        ("read_file", {"path": ["a.py"], "start": 1, "end": 3}, "path"),
        ("get_owners", {}, "exactly one"),
        ("get_owners", {"path": "a.py", "symbol_key": KEY}, "exactly one"),
        ("get_impact", {"symbol_key": KEY, "limit": 31}, "limit"),
        ("get_impact", {"symbol_key": KEY, "limit": 2.5}, "limit"),
    ],
)
def test_arguments_that_do_not_fit_are_refused_before_anything_runs(name, arguments, problem):
    with pytest.raises(ToolError, match="Invalid arguments") as error:
        box().parse(name, arguments)
    assert problem in str(error.value)
    assert error.value.allowed  # a mistake, not a call outside what tools may touch
    with pytest.raises(ToolError):
        box().call(name, arguments)  # the store is None: reaching it would raise something else


@pytest.mark.parametrize("name", ["run_command", "fetch_url", "final_answer", "", "read_file "])
def test_an_unknown_tool_is_refused_as_not_allowed(name):
    with pytest.raises(ToolError, match="no tool named") as error:
        box().call(name, {"command": "rm -rf /"})
    assert error.value.allowed is False


def test_arguments_must_be_an_object_and_errors_do_not_echo_the_input():
    with pytest.raises(ToolError, match="JSON object"):
        box().call("search_code", cast(Any, ["cart"]))
    with pytest.raises(ToolError) as error:
        box().parse("search_code", {"query": "cart", "k": "SECRET-VALUE"})
    assert "SECRET-VALUE" not in str(error.value)


def test_a_read_may_ask_for_more_than_the_cap_and_is_cut_when_it_runs():
    args = ReadFileArgs(path="a.py", start=1, end=10_000)
    assert args.end - args.start + 1 > MAX_READ_LINES  # allowed here; read_file returns 200


def test_numbered_text_keeps_every_line_and_aligns_the_numbers():
    assert numbered("a\n\nb", 9) == " 9 | a\n10 | \n11 | b"
    assert numbered("only\n", 3) == "3 | only"
    assert numbered("", 1) == "1 | "
