"""Symbol lookups shared by the resolvers: by key, by scope, by qualified name, and by member
name across the repository (the candidates of name-matched calls)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from repoatlas.ingest.inventory import Language
from repoatlas.parsing.models import ParsedFile, Symbol

Family = str  # "py", or "js" for TypeScript and JavaScript, which call each other freely

# The signature of a function assigned to a member: `res.json = function (…)`.
_ASSIGNED_MEMBER = re.compile(r"[\w$]+(?:\.[\w$]+)+\s*=")


def family(language: str) -> Family:
    return "py" if language == "py" else "js"


class SymbolIndex:
    def __init__(self, files: Sequence[ParsedFile], *, test_paths: Iterable[str] = ()) -> None:
        self.symbols: dict[str, Symbol] = {}
        self.path_of: dict[str, str] = {}
        self.language: dict[str, Language] = {}
        self.test_paths = frozenset(test_paths)
        self._children: dict[str, dict[str, Symbol]] = {}
        self._top: dict[str, dict[str, Symbol]] = {}
        self._qualnames: dict[str, dict[str, Symbol]] = {}
        self._members: dict[tuple[Family, str], dict[str, Symbol]] = {}
        self._module_level: dict[tuple[Family, str], dict[str, Symbol]] = {}
        self._local_definers: set[str] = set()
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
                if symbol.kind == "test":
                    continue
                if symbol.parent is None and symbol.qualname == symbol.name:
                    self._module_level.setdefault((fam, symbol.name), {})[parsed.path] = symbol
                    continue
                definer = self._definer(parsed, symbol)
                if definer is not None:
                    self._members.setdefault((fam, symbol.name), {})[definer] = symbol
                    if self._is_local(definer):
                        self._local_definers.add(definer)

    def _definer(self, parsed: ParsedFile, symbol: Symbol) -> str | None:
        """The class or object that defines ``symbol`` as an instance member, or ``None``.

        Members are methods of classes, and functions assigned onto objects or prototypes
        (``res.json = function``, ``Router.prototype.route = function``), the JavaScript way of
        adding methods to instances. Module-level functions, object-literal functions, and
        namespace functions are not: they are reached through bindings (T1/T2), and as name-match
        candidates they mostly catch builtin methods (``items.find()`` against ``util.find``).
        """
        if symbol.parent is not None:
            parent = self.symbols.get(symbol.parent)
            return symbol.parent if parent is not None and parent.kind == "class" else None
        if symbol.qualname != symbol.name and _ASSIGNED_MEMBER.match(symbol.signature):
            return f"{parsed.path}:{symbol.qualname.rsplit('.', 1)[0]}"
        return None

    def _is_local(self, definer: str) -> bool:
        """Whether ``definer`` is a class defined inside a function (reachable in its file only)."""
        cls = self.symbols.get(definer)
        current = cls.parent if cls is not None else None
        while current is not None:
            outer = self.symbols.get(current)
            if outer is None:
                return False
            if outer.kind != "class":
                return True
            current = outer.parent
        return False

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

    def member_candidates(self, language: str, name: str, caller_path: str) -> list[Symbol]:
        """Members named ``name`` a call in ``caller_path`` could reach, one per defining class
        or object, sorted by key.

        Members of classes defined inside functions count only for callers in the same file, and
        members defined in test files only for callers in test files: production code does not
        call into tests.
        """
        from_test = caller_path in self.test_paths
        found = self._members.get((family(language), name), {})
        reachable = [
            symbol
            for definer, symbol in found.items()
            if (from_test or self.path_of[symbol.key] not in self.test_paths)
            and (definer not in self._local_definers or self.path_of[symbol.key] == caller_path)
        ]
        return sorted(reachable, key=lambda symbol: symbol.key)

    def module_candidates(self, language: str, name: str) -> list[Symbol]:
        """Module-level functions and classes named ``name`` (for names no scope binds)."""
        found = self._module_level.get((family(language), name), {})
        return sorted(found.values(), key=lambda symbol: symbol.key)
