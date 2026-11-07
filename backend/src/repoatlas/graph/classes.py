"""Class hierarchies (GRA-6): base classes resolved through bindings, and method lookup in
method resolution order (left to right, depth first, each class once).

Only ``extends`` relations count: TypeScript ``implements`` names interfaces, which are not
symbols. Generic arguments are ignored (``Base[T]``, ``Base<T>``). A base that is not a class in
the repository — external, builtin (other than Python's ``object``), or an expression such as
``mixin(Base)`` — is an unresolved base.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from repoatlas.graph.index import SymbolIndex
from repoatlas.graph.model import Provenance
from repoatlas.graph.names import NameResolver
from repoatlas.parsing.models import ParsedFile, Range, Symbol

DOTTED_NAME = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")


@dataclass(frozen=True, slots=True)
class BaseLink:
    base: str  # the base class's key
    provenance: Provenance  # tree_sitter for a base in the same file, import_resolver otherwise
    site: Range


def base_name(text: str, language: str) -> str:
    """The dotted name of a base-class expression, without generic arguments."""
    cut = text.find("[" if language == "py" else "<")
    return (text if cut < 0 else text[:cut]).strip()


class ClassHierarchy:
    def __init__(self, index: SymbolIndex) -> None:
        self.index = index
        self.bases: dict[str, list[BaseLink]] = {}
        self.unresolved: dict[str, list[str]] = {}
        self._mro: dict[str, list[str]] = {}

    @classmethod
    def build(cls, files: Sequence[ParsedFile], names: NameResolver) -> ClassHierarchy:
        hierarchy = cls(names.index)
        for parsed in files:
            for base in parsed.class_bases:
                symbol = names.index.symbols.get(base.class_key)
                if base.relation != "extends" or symbol is None:
                    continue
                text = base_name(base.text, parsed.language)
                resolved = (
                    names.resolve_chain(parsed.path, text.split("."), symbol.parent)
                    if DOTTED_NAME.fullmatch(text)
                    else None
                )
                target = resolved.target if resolved is not None else None
                if (
                    resolved is not None
                    and target is not None
                    and target.kind == "symbol"
                    and target.key is not None
                    and target.key != base.class_key
                    and names.index.kind(target.key) == "class"
                ):
                    same_file = resolved.via == "definition" and target.path == parsed.path
                    provenance = Provenance.TREE_SITTER if same_file else Provenance.IMPORT_RESOLVER
                    link = BaseLink(target.key, provenance, base.range)
                    hierarchy.bases.setdefault(base.class_key, []).append(link)
                elif not (parsed.language == "py" and text == "object"):
                    hierarchy.unresolved.setdefault(base.class_key, []).append(base.text)
        return hierarchy

    def mro(self, class_key: str) -> list[str]:
        """The class, then its bases depth first, left to right; each class once (cycle-safe)."""
        cached = self._mro.get(class_key)
        if cached is not None:
            return cached
        order: list[str] = []
        seen: set[str] = set()
        stack = [class_key]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            stack.extend(reversed([link.base for link in self.bases.get(current, [])]))
        self._mro[class_key] = order
        return order

    def find(self, class_key: str, name: str, *, skip_self: bool = False) -> Symbol | None:
        """The member ``name`` that ``class_key`` defines or inherits (``skip_self``: bases only,
        as for ``super()``)."""
        for current in self.mro(class_key)[1 if skip_self else 0 :]:
            member = self.index.child(current, name)
            if member is not None:
                return member
        return None

    def has_unresolved_bases(self, class_key: str) -> bool:
        """Whether the class or any of its resolved bases has a base outside the repository."""
        return any(current in self.unresolved for current in self.mro(class_key))
