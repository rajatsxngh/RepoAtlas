from __future__ import annotations

import ast
import inspect

import pytest

from repoatlas.sandbox.mutate import (
    PY_RAISE,
    TS_THROW,
    SkipTarget,
    mutate_file,
    mutate_python,
    mutate_ts,
)
from repoatlas.sandbox.parsed import parse_bytes

PY = '''\
import abc
import functools
from typing import overload


def plain(a, b):
    """Add."""
    total = a + b
    return total  # trailing comment


@functools.lru_cache(maxsize=None)
def decorated(x):
    return x * 2


def one_liner(x): return x


async def fetch(url):
    data = await get(url)
    return data


def numbers(n):
    for i in range(n):
        yield i


class Shape(abc.ABC):
    def __init__(self, size):
        self.size = size

    def __repr__(self):
        return f"Shape({self.size})"

    @abc.abstractmethod
    def area(self):
        raise NotImplementedError

    @property
    def size2(self):
        return self.size * 2

    @size2.setter
    def size2(self, value):
        self.size = value / 2

    def scale(self, k):
        def inner(v):
            return v * k
        return inner(self.size)


@overload
def conv(x: int) -> int:
    ...


def conv(x):
    return x
'''


def _key(source: str, path: str, qualname: str, ordinal: int = 1) -> str:
    matches = [s for s in parse_bytes(path, source.encode()).symbols if s.qualname == qualname]
    return matches[ordinal - 1].key


def _py(qualname: str, ordinal: int = 1) -> str:
    return mutate_python(PY, _key(PY, "pkg/mod.py", qualname, ordinal))


def _function_source(mutated: str, name: str, ordinal: int = 1) -> str:
    tree = ast.parse(mutated)
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    ]
    return ast.get_source_segment(mutated, found[ordinal - 1]) or ""


def test_python_plain_function_body_is_replaced() -> None:
    mutated = _py("plain")
    assert _function_source(mutated, "plain") == f"def plain(a, b):\n    {PY_RAISE}"
    assert "# trailing comment" in mutated  # only the statements are replaced
    # Everything else is untouched.
    assert (
        mutated.replace(f"    {PY_RAISE}", '    """Add."""\n    total = a + b\n    return total')
        == PY
    )


def test_python_decorators_and_signature_are_kept() -> None:
    mutated = _py("decorated")
    assert f"@functools.lru_cache(maxsize=None)\ndef decorated(x):\n    {PY_RAISE}\n" in mutated


def test_python_async_function_and_method() -> None:
    assert f"async def fetch(url):\n    {PY_RAISE}\n" in _py("fetch")
    assert f"    def scale(self, k):\n        {PY_RAISE}\n" in _py("Shape.scale")


def test_python_generator_stays_a_generator() -> None:
    mutated = _py("numbers")
    namespace: dict[str, object] = {}
    exec(compile(mutated, "m.py", "exec"), namespace)
    numbers = namespace["numbers"]
    assert inspect.isgeneratorfunction(numbers)
    with pytest.raises(RuntimeError, match="repoatlas-mutation"):
        next(numbers(3))


def test_python_property_setter_by_ordinal() -> None:
    mutated = _py("Shape.size2", ordinal=2)
    assert "        return self.size * 2\n" in mutated  # the getter is untouched
    assert f"    def size2(self, value):\n        {PY_RAISE}\n" in mutated


def test_python_nested_function_is_replaced_with_its_parent() -> None:
    mutated = _py("Shape.scale")
    assert "def inner" not in mutated


@pytest.mark.parametrize(
    ("qualname", "ordinal", "reason"),
    [
        ("one_liner", 1, "under 2 lines"),
        ("Shape.__init__", 1, "constructor"),
        ("Shape.__repr__", 1, "dunder method"),
        ("Shape.area", 1, "abstract method"),
        ("conv", 1, "overload declaration"),
        ("Shape", 1, "not a function"),
    ],
)
def test_python_skip_rules(qualname: str, ordinal: int, reason: str) -> None:
    with pytest.raises(SkipTarget, match=reason):
        _py(qualname, ordinal)


def test_python_overload_implementation_is_mutated() -> None:
    mutated = _py("conv", ordinal=2)
    assert f"def conv(x):\n    {PY_RAISE}\n" in mutated
    assert "def conv(x: int) -> int:\n    ...\n" in mutated


def test_unknown_key_is_skipped() -> None:
    with pytest.raises(SkipTarget, match="not found"):
        mutate_python(PY, "py:pkg/mod.py:missing:function")


TS = """\
export function add(a: number, b: number): number {
  const total = a + b;
  return total;
}

export const double = (n: number) =>
  add(n, n);

export const wrap = async (n: number) => {
  await tick();
  return n;
};

export const pair = (a: number) =>
  ({ a, b: a });

export function* range(n: number) {
  for (let i = 0; i < n; i++) yield i;
}

const short = (x: number) => x;

export class Counter {
  private value = 0;

  constructor(start: number) {
    this.value = start;
  }

  bump(by = 1): number {
    this.value += by;
    return this.value;
  }

  reset = () => {
    this.value = 0;
  };
}

export function over(x: string): string;
export function over(x: number): number;
export function over(x: unknown): unknown {
  return x;
}
"""

BLOCK = "{ " + TS_THROW + " }"


def _ts(qualname: str, ordinal: int = 1) -> str:
    return mutate_ts(TS, _key(TS, "src/lib.ts", qualname, ordinal))


def test_ts_function_declaration() -> None:
    assert f"export function add(a: number, b: number): number {BLOCK}\n" in _ts("add")


def test_ts_arrow_expression_body_becomes_a_block() -> None:
    assert f"export const double = (n: number) =>\n  {BLOCK};\n" in _ts("double")
    assert f"export const pair = (a: number) =>\n  {BLOCK};\n" in _ts("pair")


def test_ts_async_arrow_block() -> None:
    assert f"export const wrap = async (n: number) => {BLOCK};\n" in _ts("wrap")


def test_ts_generator_keeps_a_yield() -> None:
    assert "export function* range(n: number) { " + TS_THROW + " yield; }" in _ts("range")


def test_ts_methods_and_field_arrows() -> None:
    mutated = _ts("Counter.bump")
    assert f"  bump(by = 1): number {BLOCK}\n" in mutated
    assert "this.value = start;" in mutated  # the constructor is untouched
    assert f"  reset = () => {BLOCK};\n" in _ts("Counter.reset")


def test_ts_overload_implementation_is_mutated() -> None:
    keys = [s.qualname for s in parse_bytes("src/lib.ts", TS.encode()).symbols]
    ordinal = keys.count("over")
    mutated = _ts("over", ordinal)
    assert f"export function over(x: unknown): unknown {BLOCK}\n" in mutated
    assert "export function over(x: string): string;\n" in mutated


@pytest.mark.parametrize(
    ("qualname", "reason"),
    [("short", "under 2 lines"), ("Counter.constructor", "constructor"), ("Counter", "not a")],
)
def test_ts_skip_rules(qualname: str, reason: str) -> None:
    with pytest.raises(SkipTarget, match=reason):
        _ts(qualname)


def test_mutate_file_builds_a_mutation() -> None:
    key = _key(PY, "pkg/mod.py", "plain")
    mutation = mutate_file("pkg/mod.py", PY.encode(), key)
    assert mutation.symbol_key == key
    assert mutation.kind == "raise"
    assert PY_RAISE.encode() in mutation.content
    with pytest.raises(ValueError, match="is not in"):
        mutate_file("other.py", PY.encode(), key)
