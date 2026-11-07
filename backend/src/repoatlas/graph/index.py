"""Symbol lookups shared by the resolvers: by key, by scope, by qualified name, and by member
name across the repository (the candidates of name-matched calls)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from repoatlas.ingest.inventory import Language
from repoatlas.parsing.models import ParsedFile, Symbol

Family = str  # "py", or "js" for TypeScript and JavaScript, which call each other freely


def family(language: str) -> Family:
    return "py" if language == "py" else "js"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A symbol that a name-matched call could reach, with the class or module defining it."""

    symbol: Symbol
    definer: str


class SymbolIndex:
    def __init__(self, files: Sequence[ParsedFile]) -> None:
        self.symbols: dict[str, Symbol] = {}
        self.path_of: dict[str, str] = {}
        self.language: dict[str, Language] = {}
        self._children: dict[str, dict[str, Symbol]] = {}
        self._top: dict[str, dict[str, Symbol]] = {}
        self._qualnames: dict[str, dict[str, Symbol]] = {}
        self._members: dict[tuple[Family, str], dict[str, Symbol]] = {}
        self._module_level: dict[tuple[Family, str], dict[str, Symbol]] = {}
        self._chains: dict[str, list[str]] = {}
        for parsed in files:
            self.language[parsed.path] = parsed.language
            top = self._top.setdefault(parsed.path, {})
            qualnames = self._qualnames.setdefault(parsed.path, {})
            fam = family(parsed.language)
            for symbol in parsed.symbols:  # source order, so later definitions win
                self.symbols[symbol.key] = symbol
                self.path_of[symbol.key] = parsed.path
                qualnames[symbol.qualname] = symbol
                if symbol.parent is not None:
                    self._children.setdefault(symbol.parent, {})[symbol.name] = symbol
                elif symbol.qualname == symbol.name and symbol.kind != "test":
                    top[symbol.name] = symbol
            for symbol in parsed.symbols:
                definer = self._definer(parsed, symbol)
                if definer is None:
                    continue
                self._members.setdefault((fam, symbol.name), {})[definer] = symbol
                module_level = symbol.parent is None and symbol.qualname == symbol.name
                if module_level:
                    self._module_level.setdefault((fam, symbol.name), {})[definer] = symbol

    def _definer(self, parsed: ParsedFile, symbol: Symbol) -> str | None:
        """Who defines ``symbol`` as a member reachable as ``obj.name``; ``None`` if nobody."""
        if symbol.kind == "test":
            return None
        if symbol.parent is not None:
            parent = self.symbols.get(symbol.parent)
            return symbol.parent if parent is not None and parent.kind == "class" else None
        if symbol.qualname != symbol.name:  # TS object-literal and assigned members: api.get
            return f"{parsed.path}:{symbol.qualname.rsplit('.', 1)[0]}"
        if parsed.language != "py" and not symbol.exported and symbol.kind != "class":
            return None  # a module's private function can't be reached as obj.name
        return parsed.path

    # -- scopes ----------------------------------------------------------------------------

    def kind(self, key: str) -> str | None:
        symbol = self.symbols.get(key)
        return symbol.kind if symbol is not None else None

    def scope_chain(self, enclosing: str | None) -> list[str]:
        """``enclosing`` and the symbols around it, innermost first (module scope not included).
        The list is shared; don't modify it."""
        if enclosing is None:
            return []
        chain = self._chains.get(enclosing)
        if chain is None:
            symbol = self.symbols.get(enclosing)
            chain = [] if symbol is None else [enclosing, *self.scope_chain(symbol.parent)]
            self._chains[enclosing] = chain
        return chain

    def enclosing_class(self, enclosing: str | None) -> Symbol | None:
        for key in self.scope_chain(enclosing):
            symbol = self.symbols[key]
            if symbol.kind == "class":
                return symbol
        return None

    def child(self, parent: str, name: str) -> Symbol | None:
        return self._children.get(parent, {}).get(name)

    def top(self, path: str, name: str) -> Symbol | None:
        return self._top.get(path, {}).get(name)

    def by_qualname(self, path: str, qualname: str) -> Symbol | None:
        return self._qualnames.get(path, {}).get(qualname)

    # -- name matching ---------------------------------------------------------------------

    def member_candidates(self, language: str, name: str) -> list[Symbol]:
        """Symbols reachable as ``obj.name``: one per defining class or module, sorted by key."""
        found = self._members.get((family(language), name), {})
        return sorted(found.values(), key=lambda symbol: symbol.key)

    def module_candidates(self, language: str, name: str) -> list[Symbol]:
        """Module-level functions and classes named ``name`` (for names no scope binds)."""
        found = self._module_level.get((family(language), name), {})
        return sorted(found.values(), key=lambda symbol: symbol.key)
