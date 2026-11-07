"""TypeScript/JavaScript extraction (ING-8).

Covers ES modules and CommonJS, classes (fields, accessors, private names, decorators), function
overloads, namespaces, React-style wrapped components and JSX usage, Express route calls, and
``describe``/``it`` test trees. Anonymous callbacks are not symbols: their calls belong to the
nearest enclosing symbol (or test).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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

FUNCTION_VALUES = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function"}
)
CLASS_DECLARATIONS = frozenset({"class_declaration", "abstract_class_declaration"})
FUNCTION_DECLARATIONS = frozenset({"function_declaration", "generator_function_declaration"})
SKIPPED = frozenset(
    {
        "function_signature",
        "method_signature",
        "abstract_method_signature",
        "index_signature",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "ambient_declaration",
        "comment",
    }
)
ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "all"})
TEST_FUNCTIONS = frozenset({"it", "test"})
SUITE_FUNCTIONS = frozenset({"describe", "suite", "context"})
TEST_MODIFIERS = frozenset({"only", "skip", "concurrent", "todo", "fails", "sequential"})
_MODIFIER_TOKENS = {"static": "static", "async": "async", "get": "getter", "set": "setter"}


@dataclass
class _Scope:
    key: str | None  # None for namespaces, which prefix names but are not symbols
    qualname: str
    kind: SymbolKind | None
    describe: tuple[str, ...] = ()


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
    exported: bool
    decorators: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)


Item = tuple[Node, _Scope | None]


class TypeScriptExtractor:
    def __init__(
        self, source: bytes, path: str, *, lang: Literal["ts", "js"], is_test: bool
    ) -> None:
        self.src = source
        self.path = path
        self.is_test = is_test
        self.keys = KeyAllocator(lang, path)
        self.syms: list[_Sym] = []
        self.by_key: dict[str, _Sym] = {}
        self.imports: list[ImportStmt] = []
        self.calls: list[CallSite] = []
        self.bases: list[ClassBase] = []
        self.routes: list[RouteCandidate] = []
        self.tests: list[TestCase] = []
        self.exports: list[Export] = []
        self.export_locals: set[str] = set()

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
        stack: list[Item] = [(root, None)]
        while stack:
            node, scope = stack.pop()
            stack.extend(reversed(self._visit(node, scope)))
        symbols = [self._finish(sym) for sym in self.syms]
        position = lambda item: (item.range.start_line, item.range.start_col)  # noqa: E731
        self.imports.sort(key=position)
        self.calls.sort(key=position)
        return (
            symbols,
            self.imports,
            self.calls,
            self.bases,
            self.routes,
            self.tests,
            self.exports,
        )

    # -- dispatch ------------------------------------------------------------------------------

    def _visit(self, node: Node, scope: _Scope | None) -> list[Item]:
        kind = node.type
        if kind in SKIPPED:
            return []
        if kind == "ERROR":
            return self._recover(node, scope)
        if kind == "export_statement":
            return self._export_statement(node, scope)
        if kind == "import_statement":
            self._import_statement(node, scope)
            return []
        if kind in FUNCTION_DECLARATIONS:
            return self._function_declaration(node, node, scope, exported=False)
        if kind in CLASS_DECLARATIONS:
            return self._class(node, node, scope, exported=False, decorators=[])
        if kind in ("lexical_declaration", "variable_declaration"):
            return self._declaration(node, node, scope, exported=False)
        if kind == "expression_statement" and scope is None:
            handled = self._module_assignment(node)
            if handled is not None:
                return handled
        if kind == "internal_module":
            return self._namespace(node, scope)
        if kind == "call_expression":
            return self._call_expression(node, scope)
        if kind == "new_expression":
            self._record_call(node, node.child_by_field_name("constructor"), scope, "new")
        if kind in ("jsx_opening_element", "jsx_self_closing_element"):
            self._jsx(node, scope)
        return [(child, scope) for child in node.named_children]

    def _recover(self, node: Node, scope: _Scope | None) -> list[Item]:
        """Inside a syntax error, named function expressions are usually broken declarations."""
        items: list[Item] = []
        for child in node.named_children:
            if child.type in ("function_expression", "generator_function") and (
                child.child_by_field_name("name") is not None
            ):
                items += self._function_declaration(child, child, scope, exported=False)
            else:
                items.append((child, scope))
        return items

    # -- declarations -------------------------------------------------------------------------

    def _function_declaration(
        self, node: Node, span: Node, scope: _Scope | None, *, exported: bool
    ) -> list[Item]:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            return [(child, scope) for child in node.named_children]
        sym = self._add(
            name=node_text(self.src, name_node),
            kind=self._function_kind(scope),
            scope=scope,
            name_node=name_node,
            span=span,
            signature=self._header(node, body),
            exported=exported,
            modifiers=_function_modifiers(node),
        )
        return self._function_children(node, sym, scope)

    def _declaration(
        self, node: Node, span: Node, scope: _Scope | None, *, exported: bool
    ) -> list[Item]:
        items: list[Item] = []
        declarators = [c for c in node.named_children if c.type == "variable_declarator"]
        for declarator in declarators:
            name_node = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name_node is None or value is None:
                continue
            if name_node.type != "identifier":
                if not self._maybe_require(value, scope, pattern=name_node):
                    items.append((value, scope))
                continue
            name = node_text(self.src, name_node)
            symbol_span = span if len(declarators) == 1 else declarator
            if exported:
                self._export(name, name, name_node)
            if value.type in FUNCTION_VALUES:
                items += self._value_function(name, name_node, value, symbol_span, scope, exported)
            elif value.type == "class":
                items += self._class(
                    value,
                    symbol_span,
                    scope,
                    exported=exported,
                    decorators=[],
                    name=name,
                    name_node=name_node,
                )
            elif value.type == "object" and scope is None:
                items += self._object_methods(name, value, scope, exported)
            elif value.type == "call_expression" and scope is None and _wrapped_function(value):
                items += self._wrapped(name, name_node, value, symbol_span, scope, exported)
            elif not self._maybe_require(value, scope, local=name):
                items.append((value, scope))
        return items

    def _value_function(
        self,
        name: str,
        name_node: Node,
        value: Node,
        span: Node,
        scope: _Scope | None,
        exported: bool,
        *,
        modifiers: list[str] | None = None,
        header_from: Node | None = None,
    ) -> list[Item]:
        body = value.child_by_field_name("body")
        header_end = body.start_byte if body is not None else value.end_byte
        header_start = (header_from or name_node).start_byte
        header = self.src[header_start:header_end].decode("utf-8", "replace")
        sym = self._add(
            name=name,
            kind=self._function_kind(scope),
            scope=scope,
            name_node=name_node,
            span=span,
            signature=collapse(header.rstrip().removesuffix("{")),
            exported=exported,
            modifiers=[*(modifiers or []), *_function_modifiers(value)],
        )
        return self._function_children(value, sym, scope)

    def _wrapped(
        self,
        name: str,
        name_node: Node,
        call: Node,
        span: Node,
        scope: _Scope | None,
        exported: bool,
    ) -> list[Item]:
        """``const Button = memo(function Button() {…})`` → a symbol for the inner function."""
        self._record_call(call, call.child_by_field_name("function"), scope, "call")
        arguments = call.child_by_field_name("arguments")
        items: list[Item] = []
        inner_done = False
        for arg in arguments.named_children if arguments is not None else []:
            if arg.type in FUNCTION_VALUES and not inner_done:
                items += self._value_function(
                    name, name_node, arg, span, scope, exported, modifiers=["wrapped"]
                )
                inner_done = True
            else:
                items.append((arg, scope))
        return items

    def _object_methods(
        self, name: str, obj: Node, scope: _Scope | None, exported: bool
    ) -> list[Item]:
        items: list[Item] = []
        for member in obj.named_children:
            if member.type == "method_definition":
                key_node = member.child_by_field_name("name")
                body = member.child_by_field_name("body")
                if key_node is None or body is None:
                    continue
                sym = self._add(
                    name=self._property_name(key_node),
                    kind="function",
                    scope=scope,
                    name_node=key_node,
                    span=member,
                    signature=self._header(member, body),
                    exported=exported,
                    modifiers=_method_modifiers(member),
                    qualname=f"{name}.{self._property_name(key_node)}",
                )
                items += self._function_children(member, sym, scope)
            elif member.type == "pair":
                key_node = member.child_by_field_name("key")
                value = member.child_by_field_name("value")
                if key_node is not None and value is not None and value.type in FUNCTION_VALUES:
                    prop = self._property_name(key_node)
                    body = value.child_by_field_name("body")
                    sym = self._add(
                        name=prop,
                        kind="function",
                        scope=scope,
                        name_node=key_node,
                        span=member,
                        signature=collapse(
                            node_text(self.src, member)[: (body.start_byte - member.start_byte)]
                            if body is not None
                            else node_text(self.src, member)
                        ),
                        exported=exported,
                        modifiers=_function_modifiers(value),
                        qualname=f"{name}.{prop}",
                    )
                    items += self._function_children(value, sym, scope)
                elif value is not None:
                    items.append((value, scope))
            else:
                items.append((member, scope))
        return items

    def _class(
        self,
        node: Node,
        span: Node,
        scope: _Scope | None,
        *,
        exported: bool,
        decorators: list[Node],
        name: str | None = None,
        name_node: Node | None = None,
    ) -> list[Item]:
        own_name = node.child_by_field_name("name")
        name_node = own_name or name_node or node
        name = node_text(self.src, own_name) if own_name is not None else (name or "default")
        body = node.child_by_field_name("body")
        all_decorators = [*decorators, *(c for c in node.children if c.type == "decorator")]
        items: list[Item] = [(d, scope) for d in all_decorators]
        header_end = body.start_byte if body is not None else node.end_byte
        sym = self._add(
            name=name,
            kind="class",
            scope=scope,
            name_node=name_node,
            span=span,
            signature=collapse(
                self.src[node.start_byte : header_end].decode("utf-8", "replace").strip()
            ),
            exported=exported,
            decorators=[self._decorator_text(d) for d in all_decorators],
            modifiers=["abstract"] if node.type == "abstract_class_declaration" else [],
        )
        items += self._heritage(node, sym.key, scope)
        if body is None:
            return items
        inner = _Scope(key=sym.key, qualname=sym.qualname, kind="class")
        pending: list[Node] = []
        for member in body.named_children:
            if member.type == "decorator":
                pending.append(member)
                items.append((member, inner))
                continue
            if member.type == "method_definition":
                items += self._method(member, sym, inner, pending)
            elif member.type in ("public_field_definition", "field_definition"):
                items += self._field(member, sym, inner, pending)
            elif member.type not in SKIPPED:
                items.append((member, inner))
            pending = []
        return items

    def _method(self, node: Node, cls: _Sym, scope: _Scope, decorators: list[Node]) -> list[Item]:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            return []
        sym = self._add(
            name=self._property_name(name_node),
            kind="method",
            scope=scope,
            name_node=name_node,
            span=node,
            signature=self._header(node, body),
            exported=False,
            decorators=[self._decorator_text(d) for d in decorators],
            modifiers=_method_modifiers(node),
        )
        return self._function_children(node, sym, scope)

    def _field(self, node: Node, cls: _Sym, scope: _Scope, decorators: list[Node]) -> list[Item]:
        name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
        value = node.child_by_field_name("value")
        if name_node is None or value is None:
            return []
        if value.type not in FUNCTION_VALUES:
            return [(value, scope)]
        body = value.child_by_field_name("body")
        header_end = body.start_byte if body is not None else value.end_byte
        sym = self._add(
            name=self._property_name(name_node),
            kind="method",
            scope=scope,
            name_node=name_node,
            span=node,
            signature=collapse(
                self.src[node.start_byte : header_end].decode("utf-8", "replace").rstrip()
            ),
            exported=False,
            decorators=[self._decorator_text(d) for d in decorators],
            modifiers=["field", *_method_modifiers(node), *_function_modifiers(value)],
        )
        return self._function_children(value, sym, scope)

    def _heritage(self, node: Node, class_key: str, scope: _Scope | None) -> list[Item]:
        items: list[Item] = []
        for heritage in (c for c in node.named_children if c.type == "class_heritage"):
            clauses = heritage.named_children
            if clauses and all(c.type in ("extends_clause", "implements_clause") for c in clauses):
                for clause in clauses:
                    relation: Literal["extends", "implements"] = (
                        "extends" if clause.type == "extends_clause" else "implements"
                    )
                    targets = (
                        [clause.child_by_field_name("value")]
                        if relation == "extends"
                        else [t for t in clause.named_children if t.type != "comment"]
                    )
                    for target in targets:
                        if target is not None:
                            self._base(class_key, target, relation)
                            items.append((target, scope))
            else:  # JavaScript: class_heritage holds the expression directly
                for target in clauses:
                    self._base(class_key, target, "extends")
                    items.append((target, scope))
        return items

    def _namespace(self, node: Node, scope: _Scope | None) -> list[Item]:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            return []
        name = node_text(self.src, name_node)
        qualname = f"{scope.qualname}.{name}" if scope else name
        inner = _Scope(key=scope.key if scope else None, qualname=qualname, kind=None)
        return [(child, inner) for child in body.named_children]

    # -- exports and modules ------------------------------------------------------------------

    def _export_statement(self, node: Node, scope: _Scope | None) -> list[Item]:
        source = node.child_by_field_name("source")
        decorators = [c for c in node.children if c.type == "decorator"]
        if source is not None:
            self._reexport(node, source, scope)
            return []
        declaration = node.child_by_field_name("declaration")
        if declaration is not None:
            is_default = any(c.type == "default" for c in node.children)
            first_new = len(self.syms)
            if declaration.type in FUNCTION_DECLARATIONS:
                items = self._function_declaration(declaration, node, scope, exported=True)
            elif declaration.type in CLASS_DECLARATIONS:
                items = self._class(declaration, node, scope, exported=True, decorators=decorators)
            elif declaration.type in ("lexical_declaration", "variable_declaration"):
                return self._declaration(declaration, node, scope, exported=True)
            else:
                return [(declaration, scope)]
            if len(self.syms) > first_new:  # the declared function or class comes first
                declared = self.syms[first_new]
                self._export("default" if is_default else declared.name, declared.name, node)
            return items
        value = node.child_by_field_name("value")
        if value is not None:  # export default <expression>
            # An anonymous function or class is named by the `default` keyword.
            keyword = next((c for c in node.children if c.type == "default"), node)
            if value.type == "identifier":
                local = node_text(self.src, value)
                self._export("default", local, node)
                return []
            if value.type in FUNCTION_VALUES:
                self._export("default", "default", node)
                return self._value_function(
                    "default", keyword, value, node, scope, True, header_from=value
                )
            if value.type == "class":
                self._export("default", "default", node)
                return self._class(
                    value,
                    node,
                    scope,
                    exported=True,
                    decorators=decorators,
                    name="default",
                    name_node=keyword,
                )
            return [(value, scope)]
        for clause in (c for c in node.named_children if c.type == "export_clause"):
            for spec in clause.named_children:
                name = spec.child_by_field_name("name")
                alias = spec.child_by_field_name("alias")
                if name is not None:
                    local = node_text(self.src, name)
                    exported = node_text(self.src, alias) if alias is not None else local
                    self._export(exported, local, spec)
        return []

    def _reexport(self, node: Node, source: Node, scope: _Scope | None) -> None:
        module = self._literal(source) or ""
        bindings: list[ImportBinding] = []
        for child in node.named_children:
            if child.type == "namespace_export":
                ident = next((c for c in child.named_children if c.type == "identifier"), None)
                local = node_text(self.src, ident) if ident is not None else None
                bindings.append(ImportBinding(kind="namespace", name=None, local=local))
                if local:
                    self.exports.append(Export(name=local, local=None, range=node_range(child)))
            elif child.type == "export_clause":
                for spec in child.named_children:
                    name = spec.child_by_field_name("name")
                    alias = spec.child_by_field_name("alias")
                    if name is None:
                        continue
                    imported = node_text(self.src, name)
                    exported = node_text(self.src, alias) if alias is not None else imported
                    bindings.append(ImportBinding(kind="named", name=imported, local=exported))
                    self.exports.append(Export(name=exported, local=None, range=node_range(spec)))
        if not bindings:
            bindings.append(ImportBinding(kind="star", name=None, local=None))
        self.imports.append(
            ImportStmt(
                kind="reexport",
                module=module,
                bindings=bindings,
                type_only=any(c.type == "type" for c in node.children),
                scope=scope.key if scope else None,
                range=node_range(node),
            )
        )

    def _import_statement(self, node: Node, scope: _Scope | None) -> None:
        source = node.child_by_field_name("source")
        require_clause = next(
            (c for c in node.named_children if c.type == "import_require_clause"), None
        )
        if source is None and require_clause is not None:
            source = require_clause.child_by_field_name("source")
        if source is None:
            return
        bindings: list[ImportBinding] = []
        if require_clause is not None:
            ident = next((c for c in require_clause.named_children if c.type == "identifier"), None)
            if ident is not None:
                bindings.append(
                    ImportBinding(kind="module", name=None, local=node_text(self.src, ident))
                )
        for clause in (c for c in node.named_children if c.type == "import_clause"):
            for part in clause.named_children:
                if part.type == "identifier":
                    default_local = node_text(self.src, part)
                    bindings.append(
                        ImportBinding(kind="default", name="default", local=default_local)
                    )
                elif part.type == "namespace_import":
                    ident = next((c for c in part.named_children if c.type == "identifier"), None)
                    ns_local = node_text(self.src, ident) if ident is not None else None
                    bindings.append(ImportBinding(kind="namespace", name=None, local=ns_local))
                elif part.type == "named_imports":
                    for spec in part.named_children:
                        name = spec.child_by_field_name("name")
                        alias = spec.child_by_field_name("alias")
                        if name is None:
                            continue
                        imported = node_text(self.src, name)
                        local = node_text(self.src, alias) if alias is not None else imported
                        bindings.append(ImportBinding(kind="named", name=imported, local=local))
        self.imports.append(
            ImportStmt(
                kind="static",
                module=self._literal(source) or "",
                bindings=bindings,
                type_only=any(c.type == "type" for c in node.children),
                scope=scope.key if scope else None,
                range=node_range(node),
            )
        )

    def _maybe_require(
        self,
        value: Node,
        scope: _Scope | None,
        *,
        local: str | None = None,
        pattern: Node | None = None,
    ) -> bool:
        """Record ``require("m")`` bound to a name or a destructuring pattern; True if recorded."""
        if value.type != "call_expression" or not self._is_require(value):
            return False
        arguments = value.child_by_field_name("arguments")
        first = arguments.named_children[0] if arguments and arguments.named_children else None
        module = self._literal(first) if first is not None else None
        if module is None:
            return False
        bindings: list[ImportBinding] = []
        if local is not None:
            bindings.append(ImportBinding(kind="module", name=None, local=local))
        elif pattern is not None and pattern.type == "object_pattern":
            for prop in pattern.named_children:
                if prop.type == "shorthand_property_identifier_pattern":
                    name = node_text(self.src, prop)
                    bindings.append(ImportBinding(kind="named", name=name, local=name))
                elif prop.type == "pair_pattern":
                    key = prop.child_by_field_name("key")
                    val = prop.child_by_field_name("value")
                    if key is not None and val is not None:
                        bindings.append(
                            ImportBinding(
                                kind="named",
                                name=self._property_name(key),
                                local=node_text(self.src, val),
                            )
                        )
        self.imports.append(
            ImportStmt(
                kind="require",
                module=module,
                bindings=bindings,
                scope=scope.key if scope else None,
                range=node_range(value),
            )
        )
        return True

    def _module_assignment(self, statement: Node) -> list[Item] | None:
        """CommonJS and prototype assignments at module level; ``None`` if not one of those."""
        expression = statement.named_children[0] if statement.named_children else None
        if expression is None or expression.type != "assignment_expression":
            return None
        left = expression.child_by_field_name("left")
        right = expression.child_by_field_name("right")
        if left is None or right is None:
            return None
        target = node_text(self.src, left)
        if left.type != "member_expression" or not _plain_dotted(left):
            return None
        if target in ("module.exports", "exports"):
            if right.type == "object":
                for member in right.named_children:
                    if member.type == "shorthand_property_identifier":
                        name = node_text(self.src, member)
                        self._export(name, name, member)
                    elif member.type == "pair":
                        key = member.child_by_field_name("key")
                        value = member.child_by_field_name("value")
                        if key is not None and value is not None:
                            local = (
                                node_text(self.src, value) if value.type == "identifier" else None
                            )
                            self._export(self._property_name(key), local, member)
                return [(right, None)]
            if right.type == "identifier":
                self._export("default", node_text(self.src, right), statement)
                return []
            if right.type in FUNCTION_VALUES:
                own = right.child_by_field_name("name")
                name = node_text(self.src, own) if own is not None else "default"
                self._export("default", name, statement)
                return self._value_function(name, own or left, right, statement, None, True)
            return None
        for prefix in ("module.exports.", "exports."):
            if target.startswith(prefix) and target.count(".") == prefix.count("."):
                name = target.removeprefix(prefix)
                if right.type in FUNCTION_VALUES:
                    self._export(name, name, statement)
                    return self._value_function(
                        name, _member_property(left), right, statement, None, True, header_from=left
                    )
                if right.type == "identifier":
                    self._export(name, node_text(self.src, right), statement)
                return [(right, None)]
        if right.type in FUNCTION_VALUES and target.count(".") <= 3:
            # app.init = function () {…};  Router.prototype.route = function () {…}
            return self._assigned(target.replace(".prototype.", "."), left, right, statement)
        return None

    def _assigned(self, qualname: str, left: Node, right: Node, statement: Node) -> list[Item]:
        body = right.child_by_field_name("body")
        header_end = body.start_byte if body is not None else right.end_byte
        sym = self._add(
            name=qualname.rsplit(".", 1)[-1],
            kind="function",
            scope=None,
            name_node=_member_property(left),
            span=statement,
            signature=collapse(
                self.src[left.start_byte : header_end].decode("utf-8", "replace").rstrip()
            ),
            exported=False,
            modifiers=_function_modifiers(right),
            qualname=qualname,
        )
        return self._function_children(right, sym, None)

    def _export(self, name: str, local: str | None, node: Node) -> None:
        self.exports.append(Export(name=name, local=local, range=node_range(node)))
        if local:
            self.export_locals.add(local)

    # -- calls, tests, routes, JSX -----------------------------------------------------------

    def _call_expression(self, node: Node, scope: _Scope | None) -> list[Item]:
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if function is None:
            return [(child, scope) for child in node.named_children]
        args = [a for a in arguments.named_children if a.type != "comment"] if arguments else []
        if function.type == "import":
            module = self._literal(args[0]) if args else None
            if module is not None:
                self.imports.append(
                    ImportStmt(
                        kind="dynamic",
                        module=module,
                        scope=scope.key if scope else None,
                        range=node_range(node),
                    )
                )
            return [(a, scope) for a in args]
        if self._is_require(node):
            self._maybe_require(node, scope)
            return []
        if self.is_test:
            role = self._test_role(function)
            if role is not None:
                handled = self._test_call(node, role, args, scope)
                if handled is not None:
                    return handled
        self._route(node, function, args)
        self._record_call(node, function, scope, "call")
        return [(child, scope) for child in node.named_children]

    def _test_call(
        self, node: Node, role: Literal["test", "suite"], args: list[Node], scope: _Scope | None
    ) -> list[Item] | None:
        title = self._title(args[0]) if args else None
        if title is None:
            return None
        callback = next((a for a in args[1:] if a.type in FUNCTION_VALUES), None)
        describe = scope.describe if scope else ()
        if role == "suite":
            inner = _Scope(
                key=scope.key if scope else None,
                qualname=scope.qualname if scope else "",
                kind=scope.kind if scope else None,
                describe=(*describe, title),
            )
            return [(callback, inner)] if callback is not None else []
        title_path = [*describe, title]
        header_end = callback.start_byte if callback is not None else node.end_byte
        header = self.src[node.start_byte : header_end].decode("utf-8", "replace")
        sym = self._add(
            name=title,
            kind="test",
            scope=scope,
            name_node=args[0],
            span=node,
            signature=collapse(header.rstrip().removesuffix(",") + ")"),
            exported=False,
            qualname=" > ".join(title_path),
        )
        self.tests.append(
            TestCase(
                key=sym.key,
                framework="jest",
                title_path=title_path,
                runner_id=" > ".join([self.path, *title_path]),
                range=node_range(node),
            )
        )
        if callback is None:
            return []
        inner = _Scope(key=sym.key, qualname=sym.qualname, kind="test", describe=())
        return [(child, inner) for child in callback.named_children]

    def _test_role(self, function: Node) -> Literal["test", "suite"] | None:
        name = node_text(self.src, function)
        if function.type == "member_expression":
            obj = function.child_by_field_name("object")
            prop = function.child_by_field_name("property")
            if obj is None or prop is None or node_text(self.src, prop) not in TEST_MODIFIERS:
                return None
            name = node_text(self.src, obj)
        elif function.type != "identifier":
            return None
        if name in TEST_FUNCTIONS:
            return "test"
        if name in SUITE_FUNCTIONS:
            return "suite"
        return None

    def _route(self, node: Node, function: Node, args: list[Node]) -> None:
        if function.type != "member_expression" or len(args) < 2:
            return
        prop = function.child_by_field_name("property")
        method = node_text(self.src, prop) if prop is not None else ""
        path = self._literal(args[0])
        if method not in ROUTE_METHODS or path is None or not path.startswith(("/", "*")):
            return
        handler = args[-1]
        callee = collapse(node_text(self.src, function))
        self.routes.append(
            RouteCandidate(
                source="call",
                methods=[method.upper()],
                path=path,
                handler=(
                    collapse(node_text(self.src, handler))
                    if handler.type in ("identifier", "member_expression")
                    else None
                ),
                text=f'{callee}("{path}")',
                range=node_range(node),
            )
        )

    def _record_call(
        self,
        node: Node,
        function: Node | None,
        scope: _Scope | None,
        kind: Literal["call", "new", "jsx"],
    ) -> None:
        if function is None:
            return
        name: str | None = None
        receiver: str | None = None
        if function.type in ("identifier", "super", "this"):
            name = node_text(self.src, function)
        elif function.type == "member_expression":
            prop = function.child_by_field_name("property")
            obj = function.child_by_field_name("object")
            name = node_text(self.src, prop) if prop is not None else None
            receiver = _normalize(collapse(node_text(self.src, obj))) if obj is not None else None
        self.calls.append(
            CallSite(
                callee=_normalize(collapse(node_text(self.src, function))),
                name=name,
                receiver=receiver,
                kind=kind,
                enclosing=scope.key if scope else None,
                range=node_range(node),
            )
        )

    def _jsx(self, node: Node, scope: _Scope | None) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        text = node_text(self.src, name_node)
        if not (text[:1].isupper() or "." in text):
            return  # intrinsic elements like <div>
        name, _, _ = text.rpartition(".")
        self.calls.append(
            CallSite(
                callee=text,
                name=text.rsplit(".", 1)[-1],
                receiver=name or None,
                kind="jsx",
                enclosing=scope.key if scope else None,
                range=node_range(node),
            )
        )

    # -- helpers -------------------------------------------------------------------------------

    def _add(
        self,
        *,
        name: str,
        kind: SymbolKind,
        scope: _Scope | None,
        name_node: Node,
        span: Node,
        signature: str,
        exported: bool,
        decorators: list[str] | None = None,
        modifiers: list[str] | None = None,
        qualname: str | None = None,
    ) -> _Sym:
        if qualname is None:
            qualname = f"{scope.qualname}.{name}" if scope and scope.qualname else name
        elif scope and scope.qualname and kind != "test":
            qualname = f"{scope.qualname}.{qualname}"
        parent = self.by_key[scope.key] if scope and scope.key else None
        sym = _Sym(
            key=self.keys.allocate(qualname, kind),
            kind=kind,
            name=name,
            qualname=qualname,
            parent=parent,
            name_node=name_node,
            span_node=span,
            signature=signature,
            exported=exported,
            decorators=decorators or [],
            modifiers=modifiers or [],
        )
        self.syms.append(sym)
        self.by_key[sym.key] = sym
        return sym

    def _function_children(self, node: Node, sym: _Sym, outer: _Scope | None) -> list[Item]:
        inner = _Scope(key=sym.key, qualname=sym.qualname, kind=sym.kind)
        skip = {"name", "property"}
        return [
            (child, inner)
            for index, child in enumerate(node.children)
            if child.is_named and node.field_name_for_child(index) not in skip
        ]

    def _function_kind(self, scope: _Scope | None) -> SymbolKind:
        return "function"

    def _header(self, node: Node, body: Node) -> str:
        return collapse(
            self.src[node.start_byte : body.start_byte].decode("utf-8", "replace").rstrip()
        )

    def _decorator_text(self, decorator: Node) -> str:
        return collapse(node_text(self.src, decorator).removeprefix("@"))

    def _base(
        self, class_key: str, target: Node, relation: Literal["extends", "implements"]
    ) -> None:
        self.bases.append(
            ClassBase(
                class_key=class_key,
                text=collapse(node_text(self.src, target)),
                relation=relation,
                range=node_range(target),
            )
        )

    def _property_name(self, node: Node) -> str:
        if node.type == "string":
            return self._literal(node) or collapse(node_text(self.src, node))
        return collapse(node_text(self.src, node))

    def _is_require(self, call: Node) -> bool:
        function = call.child_by_field_name("function")
        return (
            function is not None
            and function.type == "identifier"
            and node_text(self.src, function) == "require"
        )

    def _literal(self, node: Node) -> str | None:
        """A string literal or a template string without substitutions."""
        if node.type == "string":
            return self.src[node.start_byte + 1 : node.end_byte - 1].decode("utf-8", "replace")
        if node.type == "template_string" and not any(
            c.type == "template_substitution" for c in node.named_children
        ):
            return self.src[node.start_byte + 1 : node.end_byte - 1].decode("utf-8", "replace")
        return None

    def _title(self, node: Node) -> str | None:
        """A test title: a literal, or a template string's raw text."""
        literal = self._literal(node)
        if literal is not None:
            return literal
        if node.type == "template_string":
            return collapse(
                self.src[node.start_byte + 1 : node.end_byte - 1].decode("utf-8", "replace")
            )
        return None

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
        if sym.exported:
            return True
        if sym.parent is None:
            top = sym.qualname.split(".", 1)[0]
            return sym.kind != "test" and (
                sym.name in self.export_locals or top in self.export_locals
            )
        if sym.kind == "method" and sym.parent.kind == "class" and self._exported(sym.parent):
            private = sym.name.startswith("#") or "private" in sym.modifiers
            return not private
        return False


def _function_modifiers(node: Node) -> list[str]:
    modifiers: list[str] = []
    tokens = {c.type for c in node.children if not c.is_named}
    if "async" in tokens:
        modifiers.append("async")
    if "*" in tokens or node.type in ("generator_function_declaration", "generator_function"):
        modifiers.append("generator")
    return modifiers


def _method_modifiers(node: Node) -> list[str]:
    modifiers: list[str] = []
    for child in node.children:
        if not child.is_named and child.type in _MODIFIER_TOKENS:
            modifiers.append(_MODIFIER_TOKENS[child.type])
        elif not child.is_named and child.type == "*":
            modifiers.append("generator")
        elif child.type == "accessibility_modifier":
            text = child.text.decode() if child.text else ""
            if text in ("private", "protected"):
                modifiers.append(text)
        elif child.type == "override_modifier":
            modifiers.append("override")
    return modifiers


def _wrapped_function(call: Node) -> bool:
    arguments = call.child_by_field_name("arguments")
    return arguments is not None and any(
        a.type in FUNCTION_VALUES for a in arguments.named_children
    )


def _plain_dotted(node: Node) -> bool:
    """True for ``a.b.c`` member chains of plain identifiers (no calls, computed keys, ``?.``)."""
    while node.type == "member_expression":
        if node.child_by_field_name("optional_chain") is not None:
            return False
        prop = node.child_by_field_name("property")
        if prop is None or prop.type != "property_identifier":
            return False
        obj = node.child_by_field_name("object")
        if obj is None:
            return False
        node = obj
    return node.type == "identifier"


def _member_property(node: Node) -> Node:
    """The ``c`` of ``a.b.c = …``: the token that names an assigned member."""
    return node.child_by_field_name("property") or node


def _normalize(text: str) -> str:
    return text.replace("?.", ".")
