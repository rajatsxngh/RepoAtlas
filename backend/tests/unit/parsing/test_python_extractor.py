from __future__ import annotations

from textwrap import dedent

from repoatlas.parsing.models import ImportBinding, ParsedFile, Range
from tests.unit.parsing.helpers import keys, parse_source, symbol

PATH = "pkg/mod.py"


def py(code: str, path: str = PATH, *, is_test: bool | None = None) -> ParsedFile:
    return parse_source(dedent(code).lstrip("\n"), path, is_test=is_test)


# --- symbols -----------------------------------------------------------------------------------


def test_function_symbol() -> None:
    parsed = py("""
        def create_app(config: dict | None = None) -> "App":
            return App(config)
    """)
    fn = symbol(parsed, "create_app")
    assert fn.key == "py:pkg/mod.py:create_app:function"
    assert fn.kind == "function"
    assert fn.parent is None
    assert fn.exported is True
    assert fn.signature == 'def create_app(config: dict | None = None) -> "App"'
    assert fn.name_range == Range(start_line=1, start_col=4, end_line=1, end_col=14)
    assert fn.span.start_line == 1 and fn.span.end_line == 2


def test_async_function() -> None:
    fn = symbol(py("async def fetch(url):\n    return await get(url)\n"), "fetch")
    assert fn.modifiers == ["async"]
    assert fn.signature == "async def fetch(url)"


def test_generator_detection_ignores_nested_functions() -> None:
    parsed = py("""
        def numbers():
            yield 1

        def outer():
            def inner():
                yield 2
            return inner
    """)
    assert symbol(parsed, "numbers").modifiers == ["generator"]
    assert symbol(parsed, "outer").modifiers == []
    assert symbol(parsed, "outer.inner").modifiers == ["generator"]


def test_nested_functions_are_scoped_and_private() -> None:
    parsed = py("""
        def outer():
            def inner():
                pass
    """)
    inner = symbol(parsed, "outer.inner")
    assert inner.key == "py:pkg/mod.py:outer.inner:function"
    assert inner.parent == "py:pkg/mod.py:outer:function"
    assert inner.exported is False


def test_classes_and_methods() -> None:
    parsed = py("""
        class Cart:
            def add(self, item):
                self.items.append(item)

            class Line:
                def total(self):
                    return 0
    """)
    assert keys(parsed) == [
        "py:pkg/mod.py:Cart:class",
        "py:pkg/mod.py:Cart.add:method",
        "py:pkg/mod.py:Cart.Line:class",
        "py:pkg/mod.py:Cart.Line.total:method",
    ]
    assert symbol(parsed, "Cart.add").parent == "py:pkg/mod.py:Cart:class"
    assert symbol(parsed, "Cart").signature == "class Cart"


def test_export_flags_follow_naming() -> None:
    parsed = py("""
        def public(): pass
        def _private(): pass
        class Shape:
            def __init__(self): pass
            def area(self): pass
            def _cache(self): pass
        class _Hidden:
            def visible(self): pass
    """)
    exported = {s.qualname: s.exported for s in parsed.symbols}
    assert exported == {
        "public": True,
        "_private": False,
        "Shape": True,
        "Shape.__init__": True,
        "Shape.area": True,
        "Shape._cache": False,
        "_Hidden": False,
        "_Hidden.visible": False,
    }


def test_dunder_all_controls_exports() -> None:
    parsed = py("""
        __all__ = ["create_app", 'Cart']
        __all__ += ["extra"]
        def create_app(): pass
        def helper(): pass
        class Cart: pass
    """)
    assert {s.qualname: s.exported for s in parsed.symbols} == {
        "create_app": True,
        "helper": False,
        "Cart": True,
    }
    assert [e.name for e in parsed.exports] == ["create_app", "Cart", "extra"]


def test_decorators_and_modifiers() -> None:
    parsed = py("""
        class Account:
            @property
            def balance(self): return 0

            @balance.setter
            def balance(self, value): pass

            @staticmethod
            def make(): pass

            @classmethod
            def load(cls): pass

            @functools.cached_property
            def summary(self): pass
    """)
    first, second = symbol(parsed, "Account.balance"), symbol(parsed, "Account.balance", 2)
    assert first.modifiers == ["property"]
    assert second.modifiers == ["setter"]
    assert second.key == "py:pkg/mod.py:Account.balance:method~2"
    assert second.decorators == ["balance.setter"]
    assert symbol(parsed, "Account.make").modifiers == ["staticmethod"]
    assert symbol(parsed, "Account.load").modifiers == ["classmethod"]
    assert symbol(parsed, "Account.summary").modifiers == ["property"]


def test_decorated_span_includes_decorators() -> None:
    parsed = py("""
        @cache
        @other(1)
        def f():
            pass
    """)
    fn = symbol(parsed, "f")
    assert fn.decorators == ["cache", "other(1)"]
    assert fn.span.start_line == 1
    assert fn.name_range.start_line == 3


def test_redefinitions_get_ordinals() -> None:
    parsed = py("""
        try:
            from fast import loads
        except ImportError:
            def loads(text): pass
        if VERSION > 2:
            def loads(text): pass
    """)
    assert [s.key for s in parsed.symbols] == [
        "py:pkg/mod.py:loads:function",
        "py:pkg/mod.py:loads:function~2",
    ]


def test_conditional_definitions_stay_at_module_scope() -> None:
    parsed = py("""
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            class Proto: pass
    """)
    assert symbol(parsed, "Proto").parent is None


def test_unicode_identifiers_use_byte_columns() -> None:
    fn = symbol(py("def naïve_é(): pass\n"), "naïve_é")
    assert fn.name == "naïve_é"
    assert fn.name_range == Range(start_line=1, start_col=4, end_line=1, end_col=13)


def test_multiline_signature_is_collapsed() -> None:
    fn = symbol(
        py("""
            def build(
                name: str,
                *,
                strict: bool = False,
            ) -> int:
                return 1
        """),
        "build",
    )
    assert fn.signature == "def build( name: str, *, strict: bool = False, ) -> int"


# --- imports -----------------------------------------------------------------------------------


def test_import_statements() -> None:
    parsed = py("import os.path as osp, sys\nimport a.b.c\n")
    assert [(i.module, i.bindings) for i in parsed.imports] == [
        ("os.path", [ImportBinding(kind="module", name="os.path", local="osp")]),
        ("sys", [ImportBinding(kind="module", name="sys", local="sys")]),
        ("a.b.c", [ImportBinding(kind="module", name="a.b.c", local="a")]),
    ]
    assert all(i.kind == "static" and i.level == 0 and i.scope is None for i in parsed.imports)


def test_from_imports_relative_aliased_and_star() -> None:
    parsed = py("from ..pkg.mod import a as b, c\nfrom . import x\nfrom m import *\n")
    first, second, third = parsed.imports
    assert (first.module, first.level) == ("..pkg.mod", 2)
    assert first.bindings == [
        ImportBinding(kind="named", name="a", local="b"),
        ImportBinding(kind="named", name="c", local="c"),
    ]
    assert (second.module, second.level, second.bindings[0].name) == (".", 1, "x")
    assert third.bindings == [ImportBinding(kind="star", name=None, local=None)]


def test_future_imports_are_ignored() -> None:
    assert py("from __future__ import annotations\nimport os\n").imports[0].module == "os"
    assert len(py("from __future__ import annotations\n").imports) == 0


def test_local_imports_record_their_scope() -> None:
    parsed = py("def lazy():\n    import json\n    return json\n")
    assert parsed.imports[0].scope == "py:pkg/mod.py:lazy:function"


# --- calls -------------------------------------------------------------------------------------


def test_call_sites() -> None:
    parsed = py("""
        class Repo:
            def find(self, id):
                row = self.db.query(id)
                return Model(row)
    """)
    query, model = parsed.calls
    assert (query.callee, query.name, query.receiver) == ("self.db.query", "query", "self.db")
    assert query.enclosing == "py:pkg/mod.py:Repo.find:method"
    assert (model.callee, model.name, model.receiver) == ("Model", "Model", None)
    assert query.range.start_line == 3


def test_decorator_and_default_argument_calls_belong_to_the_outer_scope() -> None:
    parsed = py("""
        def outer():
            @register("x")
            def inner(value=default_value()):
                work()
    """)
    by_name = {c.name: c.enclosing for c in parsed.calls}
    assert by_name == {
        "register": "py:pkg/mod.py:outer:function",
        "default_value": "py:pkg/mod.py:outer:function",
        "work": "py:pkg/mod.py:outer.inner:function",
    }


def test_calls_in_lambdas_and_comprehensions_belong_to_the_function() -> None:
    parsed = py("""
        def run(items):
            key = lambda x: score(x)
            return [transform(i) for i in items]
    """)
    assert {c.name for c in parsed.calls} == {"score", "transform"}
    assert {c.enclosing for c in parsed.calls} == {"py:pkg/mod.py:run:function"}


def test_calls_on_call_results() -> None:
    parsed = py("factory()()\n")
    outer, inner = sorted(parsed.calls, key=lambda c: len(c.callee), reverse=True)
    assert (outer.callee, outer.name, outer.receiver) == ("factory()", None, None)
    assert inner.name == "factory"


def test_module_level_calls_have_no_enclosing_symbol() -> None:
    parsed = py('if __name__ == "__main__":\n    main()\n')
    assert parsed.calls[0].enclosing is None


# --- classes, routes, tests --------------------------------------------------------------------


def test_class_bases_skip_keyword_arguments() -> None:
    parsed = py("class A(Base, mixins.M, Generic[T], metaclass=Meta):\n    pass\n")
    assert [b.text for b in parsed.class_bases] == ["Base", "mixins.M", "Generic[T]"]
    assert {b.class_key for b in parsed.class_bases} == {"py:pkg/mod.py:A:class"}
    assert {b.relation for b in parsed.class_bases} == {"extends"}


def test_fastapi_and_flask_route_candidates() -> None:
    parsed = py("""
        @app.get("/users/{id}", tags=["users"])
        async def get_user(id: int): ...

        @bp.route("/orders", methods=["POST", "put"])
        def orders(): ...

        @app.route("/health")
        def health(): ...

        @router.get(PATH)
        def dynamic(): ...
    """)
    routes = [(r.methods, r.path, r.handler) for r in parsed.routes]
    assert routes == [
        (["GET"], "/users/{id}", "py:pkg/mod.py:get_user:function"),
        (["POST", "PUT"], "/orders", "py:pkg/mod.py:orders:function"),
        (["GET"], "/health", "py:pkg/mod.py:health:function"),
    ]
    assert parsed.routes[0].text == 'app.get("/users/{id}", tags=["users"])'


def test_pytest_tests_in_test_files() -> None:
    parsed = py(
        """
        import pytest

        def test_add():
            assert add(1, 2) == 3

        def helper():
            pass

        class TestTotals:
            def test_empty(self):
                assert total([]) == 0
        """,
        path="tests/test_math.py",
    )
    assert {s.qualname: s.kind for s in parsed.symbols} == {
        "test_add": "test",
        "helper": "function",
        "TestTotals": "class",
        "TestTotals.test_empty": "test",
    }
    assert [t.runner_id for t in parsed.tests] == [
        "tests/test_math.py::test_add",
        "tests/test_math.py::TestTotals::test_empty",
    ]
    assert parsed.tests[1].title_path == ["TestTotals", "test_empty"]
    assert parsed.calls[0].enclosing == "py:tests/test_math.py:test_add:test"


def test_test_named_functions_outside_test_files_are_functions() -> None:
    assert symbol(py("def test_connection(): pass\n"), "test_connection").kind == "function"


# --- robustness ----------------------------------------------------------------------------------


def test_syntax_errors_are_recorded_and_extraction_continues() -> None:
    parsed = py("""
        def broken(:
            pass

        def fine():
            return ok()
    """)
    assert parsed.issue_count >= 1
    assert parsed.issues[0].kind in {"syntax_error", "missing_node"}
    assert "fine" in {s.qualname for s in parsed.symbols}


def test_byte_order_mark_is_skipped_but_counted_in_columns() -> None:
    parsed = parse_source(b"\xef\xbb\xbfdef f(): pass\n", PATH)
    assert symbol(parsed, "f").name_range.start_col == 7  # columns index the blob's bytes
    assert parsed.issue_count == 0


def test_invalid_utf8_is_reported_not_fatal() -> None:
    parsed = parse_source(b"def f():\n    return '\xff'\n", PATH)
    assert parsed.issues[0].kind == "decode_error"
    assert symbol(parsed, "f").kind == "function"


def test_output_is_deterministic() -> None:
    code = "class A:\n    def m(self): helper()\n"
    assert py(code).model_dump_json() == py(code).model_dump_json()
