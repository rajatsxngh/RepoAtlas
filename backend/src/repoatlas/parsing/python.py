"""Python extraction (ING-7): symbols, imports, calls, class bases, routes, pytest tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from repoatlas.parsing.models import (
    CallSite,
    ClassBase,
    Export,
    ImportBinding,
    ImportStmt,
    RouteCandidate,
    Symbol,
    TestCase,
)
from repoatlas.parsing.symbols import KeyAllocator, SymbolKind
from repoatlas.parsing.ts_runtime import collapse, node_range, node_text

HTTP_DECORATORS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
ROUTE_DECORATORS = HTTP_DECORATORS | {"route", "api_route", "websocket"}
_MODIFIER_DECORATORS = {
    "property": "property",
    "functools.cached_property": "property",
    "cached_property": "property",
    "staticmethod": "staticmethod",
    "classmethod": "classmethod",
    "abstractmethod": "abstract",
    "abc.abstractmethod": "abstract",
}
_SCOPE_BOUNDARIES = frozenset({"function_definition", "class_definition", "lambda"})


@dataclass
class _Scope:
    key: str
    qualname: str
    kind: SymbolKind
    title_path: list[str]


@dataclass
class _Sym:
    key: str
    kind: SymbolKind
    name: str
    qualname: str
    parent: _Sym | None
    name_node: Node
    span_node: Node
    signature: str
    decorators: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)


class PythonExtractor:
    def __init__(self, source: bytes, path: str, *, is_test: bool) -> None:
        self.src = source
        self.path = path
        self.is_test = is_test
        self.keys = KeyAllocator("py", path)
        self.syms: list[_Sym] = []
        self.by_key: dict[str, _Sym] = {}
        self.imports: list[ImportStmt] = []
        self.calls: list[CallSite] = []
        self.bases: list[ClassBase] = []
        self.routes: list[RouteCandidate] = []
        self.tests: list[TestCase] = []
        self.exports: list[Export] = []
        self.all_names: set[str] | None = None

    def run(
        self, root: Node
    ) -> tuple[
        list[Symbol],
        list[ImportStmt],
        list[CallSite],
        list[ClassBase],
        list[RouteCandidate],
        list[TestCase],
        list[Export],
    ]:
        self._collect_all(root)
        self._walk(root)
        symbols = [self._finish(sym) for sym in self.syms]
        self.imports.sort(key=lambda item: (item.range.start_line, item.range.start_col))
        self.calls.sort(key=lambda item: (item.range.start_line, item.range.start_col))
        return (
            symbols,
            self.imports,
            self.calls,
            self.bases,
            self.routes,
            self.tests,
            self.exports,
        )

    # -- traversal ---------------------------------------------------------------------------

    def _walk(self, root: Node) -> None:
        stack: list[tuple[Node, _Scope | None]] = [(root, None)]
        while stack:
            node, scope = stack.pop()
            kind = node.type
            if kind == "decorated_definition":
                decorators = [c for c in node.children if c.type == "decorator"]
                definition = node.child_by_field_name("definition")
                if definition is not None:
                    stack.extend(self._definition(definition, node, decorators, scope))
                stack.extend((d, scope) for d in reversed(decorators))
                continue
            if kind in ("function_definition", "class_definition"):
                stack.extend(self._definition(node, node, [], scope))
                continue
            if kind == "import_statement":
                self._import(node, scope)
                continue
            if kind == "import_from_statement":
                self._import_from(node, scope)
                continue
            if kind == "future_import_statement":
                continue
            if kind == "call":
                self._call(node, scope)
            stack.extend((child, scope) for child in reversed(node.named_children))

    def _definition(
        self, node: Node, span: Node, decorators: list[Node], scope: _Scope | None
    ) -> list[tuple[Node, _Scope | None]]:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            return [(child, scope) for child in reversed(node.named_children)]
        name = node_text(self.src, name_node)
        is_class = node.type == "class_definition"
        kind = self._kind(name, is_class, scope)
        qualname = f"{scope.qualname}.{name}" if scope else name
        decorator_texts = [self._decorator_text(d) for d in decorators]
        header = self.src[node.start_byte : body.start_byte].decode("utf-8", "replace")
        sym = _Sym(
            key=self.keys.allocate(qualname, kind),
            kind=kind,
            name=name,
            qualname=qualname,
            parent=self.by_key[scope.key] if scope else None,
            name_node=name_node,
            span_node=span,
            signature=collapse(header.rstrip().removesuffix(":")),
            decorators=decorator_texts,
            modifiers=self._modifiers(node, body, decorator_texts),
        )
        self.syms.append(sym)
        self.by_key[sym.key] = sym
        title_path = [*(scope.title_path if scope else []), name]
        if is_class:
            self._class_bases(node, sym.key)
        for decorator in decorators:
            self._route(decorator, sym.key)
        if kind == "test":
            self.tests.append(
                TestCase(
                    key=sym.key,
                    framework="pytest",
                    title_path=title_path,
                    runner_id="::".join([self.path, *title_path]),
                    range=node_range(span),
                )
            )
        inner = _Scope(key=sym.key, qualname=qualname, kind=kind, title_path=title_path)
        # Defaults, annotations, and base classes run in the enclosing scope; the body in ours.
        pending: list[tuple[Node, _Scope | None]] = [(body, inner)]
        for child_name in ("superclasses", "parameters", "return_type"):
            child = node.child_by_field_name(child_name)
            if child is not None:
                pending.append((child, scope))
        return pending

    def _kind(self, name: str, is_class: bool, scope: _Scope | None) -> SymbolKind:
        if is_class:
            return "class"
        in_class = scope is not None and scope.kind == "class"
        if self.is_test and name.startswith("test") and (scope is None or in_class):
            return "test"
        return "method" if in_class else "function"

    def _modifiers(self, node: Node, body: Node, decorators: list[str]) -> list[str]:
        modifiers: list[str] = []
        if node.type == "function_definition":
            if node.children and node.children[0].type == "async":
                modifiers.append("async")
            if _contains_yield(body):
                modifiers.append("generator")
        for text in decorators:
            mapped = _MODIFIER_DECORATORS.get(text)
            if mapped is None:
                for suffix in ("setter", "getter", "deleter"):
                    if text.endswith("." + suffix):
                        mapped = suffix
            if mapped and mapped not in modifiers:
                modifiers.append(mapped)
        return modifiers

    def _decorator_text(self, decorator: Node) -> str:
        expression = decorator.named_children[0] if decorator.named_children else decorator
        return collapse(node_text(self.src, expression))

    # -- records -------------------------------------------------------------------------------

    def _class_bases(self, node: Node, class_key: str) -> None:
        superclasses = node.child_by_field_name("superclasses")
        if superclasses is None:
            return
        for arg in superclasses.named_children:
            if arg.type in ("keyword_argument", "list_splat", "dictionary_splat", "comment"):
                continue
            self.bases.append(
                ClassBase(
                    class_key=class_key,
                    text=collapse(node_text(self.src, arg)),
                    relation="extends",
                    range=node_range(arg),
                )
            )

    def _route(self, decorator: Node, handler_key: str) -> None:
        expression = decorator.named_children[0] if decorator.named_children else None
        if expression is None or expression.type != "call":
            return
        function = expression.child_by_field_name("function")
        arguments = expression.child_by_field_name("arguments")
        if function is None or arguments is None:
            return
        method_name = _last_name(self.src, function)
        if method_name not in ROUTE_DECORATORS:
            return
        positional = [a for a in arguments.named_children if a.type != "keyword_argument"]
        path = self._string(positional[0]) if positional else None
        if path is None:
            return
        methods = [method_name.upper()] if method_name in HTTP_DECORATORS else ["GET"]
        if method_name == "websocket":
            methods = ["WEBSOCKET"]
        for arg in arguments.named_children:
            if arg.type != "keyword_argument":
                continue
            name = arg.child_by_field_name("name")
            value = arg.child_by_field_name("value")
            if name and value and node_text(self.src, name) == "methods":
                listed = [self._string(item) for item in value.named_children]
                methods = [m.upper() for m in listed if m] or methods
        self.routes.append(
            RouteCandidate(
                source="decorator",
                methods=methods,
                path=path,
                handler=handler_key,
                text=collapse(node_text(self.src, expression)),
                range=node_range(decorator),
            )
        )

    def _import(self, node: Node, scope: _Scope | None) -> None:
        for name_node in node.children_by_field_name("name"):
            if name_node.type == "aliased_import":
                target = name_node.child_by_field_name("name")
                alias = name_node.child_by_field_name("alias")
                module = node_text(self.src, target) if target else ""
                local = node_text(self.src, alias) if alias else None
            else:
                module = node_text(self.src, name_node)
                local = module.split(".", 1)[0]
            self.imports.append(
                ImportStmt(
                    kind="static",
                    module=module,
                    bindings=[ImportBinding(kind="module", name=module, local=local)],
                    scope=scope.key if scope else None,
                    range=node_range(node),
                )
            )

    def _import_from(self, node: Node, scope: _Scope | None) -> None:
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return
        module = node_text(self.src, module_node).replace(" ", "")
        level = len(module) - len(module.lstrip("."))
        bindings: list[ImportBinding] = []
        for child in node.named_children:
            if child.type == "wildcard_import":
                bindings.append(ImportBinding(kind="star", name=None, local=None))
        for name_node in node.children_by_field_name("name"):
            if name_node.type == "aliased_import":
                target = name_node.child_by_field_name("name")
                alias = name_node.child_by_field_name("alias")
                name = node_text(self.src, target) if target else ""
                local = node_text(self.src, alias) if alias else name
            else:
                name = local = node_text(self.src, name_node)
            bindings.append(ImportBinding(kind="named", name=name, local=local))
        self.imports.append(
            ImportStmt(
                kind="static",
                module=module,
                level=level,
                bindings=bindings,
                scope=scope.key if scope else None,
                range=node_range(node),
            )
        )

    def _call(self, node: Node, scope: _Scope | None) -> None:
        function = node.child_by_field_name("function")
        if function is None:
            return
        name: str | None = None
        receiver: str | None = None
        if function.type == "identifier":
            name = node_text(self.src, function)
        elif function.type == "attribute":
            attribute = function.child_by_field_name("attribute")
            obj = function.child_by_field_name("object")
            name = node_text(self.src, attribute) if attribute else None
            receiver = collapse(node_text(self.src, obj)) if obj else None
        self.calls.append(
            CallSite(
                callee=collapse(node_text(self.src, function)),
                name=name,
                receiver=receiver,
                kind="call",
                enclosing=scope.key if scope else None,
                range=node_range(node),
            )
        )

    def _collect_all(self, root: Node) -> None:
        """Read module-level ``__all__ = [...]`` / ``__all__ += [...]`` string lists."""
        for statement in root.named_children:
            if statement.type != "expression_statement" or not statement.named_children:
                continue
            assignment = statement.named_children[0]
            if assignment.type not in ("assignment", "augmented_assignment"):
                continue
            left = assignment.child_by_field_name("left")
            right = assignment.child_by_field_name("right")
            if left is None or right is None or node_text(self.src, left) != "__all__":
                continue
            if right.type not in ("list", "tuple"):
                continue
            if self.all_names is None:
                self.all_names = set()
            for item in right.named_children:
                value = self._string(item)
                if value is not None:
                    self.all_names.add(value)
                    self.exports.append(Export(name=value, local=value, range=node_range(item)))

    def _string(self, node: Node) -> str | None:
        """The value of a plain string literal (no f-string interpolation, no bytes)."""
        if node.type != "string" or any(c.type == "interpolation" for c in node.children):
            return None
        start = next((c for c in node.children if c.type == "string_start"), None)
        end = next((c for c in node.children if c.type == "string_end"), None)
        if start is None or end is None:
            return None
        prefix = node_text(self.src, start).lower().rstrip("\"'")
        if "b" in prefix:
            return None
        return self.src[start.end_byte : end.start_byte].decode("utf-8", "replace")

    # -- results ---------------------------------------------------------------------------------

    def _finish(self, sym: _Sym) -> Symbol:
        return Symbol(
            key=sym.key,
            kind=sym.kind,
            name=sym.name,
            qualname=sym.qualname,
            parent=sym.parent.key if sym.parent else None,
            name_range=node_range(sym.name_node),
            span=node_range(sym.span_node),
            signature=sym.signature,
            decorators=sym.decorators,
            modifiers=sym.modifiers,
            exported=self._exported(sym),
        )

    def _exported(self, sym: _Sym) -> bool:
        if sym.parent is None:
            if self.all_names is not None:
                return sym.name in self.all_names
            return not sym.name.startswith("_")
        if sym.parent.kind != "class" or not self._exported(sym.parent):
            return False
        dunder = sym.name.startswith("__") and sym.name.endswith("__")
        return dunder or not sym.name.startswith("_")


def _contains_yield(body: Node) -> bool:
    stack = list(body.named_children)
    while stack:
        node = stack.pop()
        if node.type == "yield":
            return True
        if node.type not in _SCOPE_BOUNDARIES:
            stack.extend(node.named_children)
    return False


def _last_name(source: bytes, node: Node) -> str | None:
    if node.type == "identifier":
        return node_text(source, node)
    if node.type == "attribute":
        attribute = node.child_by_field_name("attribute")
        return node_text(source, attribute) if attribute else None
    return None
