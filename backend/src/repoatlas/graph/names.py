"""Name lookup in one file: lexical scopes, imports, builtins, and dotted chains.

A bare name resolves in the innermost scope that defines or imports it: nested definitions and
local imports of each enclosing function, then the module's own definitions, then its imports
(star imports last), then the language's builtins. Definitions win over imports in the same
scope (``try: from fast import dumps`` / ``except ImportError: def dumps`` resolves to the repo
code). Class bodies don't form scopes for the functions inside them, as in Python and JS.
"""

from __future__ import annotations

import builtins as _builtins
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from repoatlas.graph.bindings import BindingTable, ModuleResolvers
from repoatlas.graph.index import SymbolIndex
from repoatlas.graph.targets import Target

if TYPE_CHECKING:
    from repoatlas.graph.classes import ClassHierarchy

PYTHON_BUILTINS = frozenset(dir(_builtins))
JS_GLOBALS = frozenset(  # browser, Node.js, and test-runner globals
    """
    AbortController AbortSignal AggregateError Array ArrayBuffer Atomics Audio BigInt
    BigInt64Array BigUint64Array Blob Boolean Buffer BroadcastChannel CustomEvent DataView Date
    DOMException Error EvalError Event EventSource EventTarget File FileReader
    FinalizationRegistry Float32Array Float64Array FormData Function Headers Image Int16Array
    Int32Array Int8Array IntersectionObserver Intl JSON Map Math MessageChannel MutationObserver
    Notification Number Object Promise Proxy RangeError ReferenceError Reflect RegExp Request
    ResizeObserver Response Set SharedArrayBuffer String Symbol SyntaxError TextDecoder
    TextEncoder TypeError URIError URL URLSearchParams Uint16Array Uint32Array Uint8Array
    Uint8ClampedArray WeakMap WeakRef WeakSet WebSocket Worker XMLHttpRequest __dirname
    __filename alert atob btoa cancelAnimationFrame clearImmediate clearInterval clearTimeout
    confirm console crypto customElements decodeURI decodeURIComponent document encodeURI
    encodeURIComponent escape eval exports fetch getComputedStyle globalThis history isFinite
    isNaN localStorage location matchMedia module navigator parseFloat parseInt performance
    process prompt queueMicrotask require requestAnimationFrame requestIdleCallback self
    sessionStorage setImmediate setInterval setTimeout structuredClone unescape window afterAll
    afterEach beforeAll beforeEach context cy describe expect fdescribe fit it jest suite test
    vi xdescribe xit xtest
    """.split()  # noqa: SIM905 - a word list reads better than 140 quoted strings
)

Via = Literal["definition", "import", "builtin"]


@dataclass(frozen=True, slots=True)
class Resolved:
    """What a name refers to, and whether a definition, an import, or a builtin bound it."""

    target: Target
    via: Via


class NameResolver:
    def __init__(
        self,
        index: SymbolIndex,
        bindings: Mapping[str, BindingTable],
        resolvers: ModuleResolvers,
    ) -> None:
        self.index = index
        self.bindings = bindings
        self.resolvers = resolvers
        # Set once base classes are resolved; until then class members are direct members only.
        self.hierarchy: ClassHierarchy | None = None

    def resolve_name(self, path: str, name: str, enclosing: str | None) -> Resolved | None:
        """``name`` as seen from inside ``enclosing`` (a symbol key; ``None`` = module level)."""
        python = self.index.language.get(path) == "py"
        table = self.bindings.get(path)
        for position, scope in enumerate(self.index.scope_chain(enclosing)):
            in_class_body = self.index.kind(scope) == "class"
            if not in_class_body or (position == 0 and python):
                child = self.index.child(scope, name)
                if child is not None and child.kind != "test":
                    return Resolved(Target.for_symbol(child.key, path), "definition")
            if table is not None:
                bound = table.lookup(name, [scope])
                if bound is not None:
                    return Resolved(bound, "import")
        top = self.index.top(path, name)
        if top is not None:
            return Resolved(Target.for_symbol(top.key, path), "definition")
        if table is not None:
            bound = table.lookup(name)
            if bound is not None:
                return Resolved(bound, "import")
        if name in (PYTHON_BUILTINS if python else JS_GLOBALS):
            return Resolved(Target.for_builtin(name), "builtin")
        return None

    def resolve_chain(self, path: str, parts: list[str], enclosing: str | None) -> Resolved | None:
        """A dotted name (``pkg.mod.Class``): its root as a name, then each part as a member."""
        root = self.resolve_name(path, parts[0], enclosing)
        if root is None:
            return None
        target: Target | None = root.target
        for part in parts[1:]:
            target = self.member_of(target, part) if target is not None else None
        return Resolved(target, root.via) if target is not None else None

    def member_of(self, target: Target, name: str) -> Target | None:
        """``target.name``; ``None`` when it can't be known statically."""
        if target.kind in ("external", "missing", "builtin"):
            return target
        if target.kind == "module" and target.module is not None:
            return self.resolvers.member(target.module, name)
        if target.kind == "symbol" and target.key is not None:
            if self.index.kind(target.key) == "class":
                return self.class_member(target.key, name)
            return None
        if target.kind == "member" and target.path and target.name:
            # An object literal or namespace object: `const api = { get() {} }` → api.get
            symbol = self.index.by_qualname(target.path, f"{target.name}.{name}")
            if symbol is not None and symbol.parent is None:
                return Target.for_symbol(symbol.key, target.path)
        return None

    def class_member(self, class_key: str, name: str) -> Target | None:
        """A member of a class or, once bases are known, of its bases (method resolution order).
        A member missing from a class with bases outside the repository is external."""
        if self.hierarchy is None:
            child = self.index.child(class_key, name)
            return Target.for_symbol(child.key, self.index.path_of[child.key]) if child else None
        found = self.hierarchy.find(class_key, name)
        if found is not None:
            return Target.for_symbol(found.key, self.index.path_of[found.key])
        if self.hierarchy.has_unresolved_bases(class_key):
            return Target.for_external(f"{class_key}.{name}")
        return None
