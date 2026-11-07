"""Call resolution by name matching (GRA-5, GRA-10; tiers in ``graph.tiers``, PRD §9.1).

For each call site, in order:

- ``f()`` resolves ``f`` as a name (``graph.names``): a definition or an import of a repository
  symbol is T1; external packages and builtins are dropped (counted); a name nothing binds is an
  unresolved ``unbound_name`` call.
- ``self.m()``/``cls.m()``/``this.m()`` look ``m`` up on the enclosing class and its resolved
  bases (T3); ``super().m()``/``super.m()`` on the bases only (T4), and ``super(…)`` in a
  constructor reaches the base constructor (T4). A member missing from a class whose bases
  include external ones is external. In an object-literal method, ``this.m()`` is a sibling.
- ``a.b.m()`` with a plain dotted receiver: ``a.b.m`` defined in the same file is T1; a receiver
  that resolves to a repository module, namespace, or class with member ``m`` is T2; one that
  resolves outside the repository is external; a literal receiver (``", ".join``) is builtin.
- Anything else is ``obj.m()`` with an unknown ``obj``: name matching against the members of the
  repository's classes and object literals in the same language family (module-level functions
  are reached through imports instead; classes defined inside functions count only within
  their file; and production code never matches members defined in test files). One defining
  class or object is T5; two to five give one T6 edge each, splitting the T6 confidence; more,
  or none, give an unresolved call.

Calling a class links to the class and to the ``__init__``/``constructor`` it defines or
inherits, at the same tier. Calling a CommonJS module object reaches its ``module.exports``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from repoatlas.graph.classes import ClassHierarchy
from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.index import SymbolIndex
from repoatlas.graph.model import EdgeKind, Provenance, UnresolvedCall, UnresolvedReason
from repoatlas.graph.names import NameResolver
from repoatlas.graph.targets import Target
from repoatlas.graph.tiers import MAX_CANDIDATES, MAX_UNRESOLVED_CANDIDATES, Tier, tier_confidence
from repoatlas.parsing.models import CallSite, ParsedFile

CallOutcome = Literal["resolved", "unresolved", "external", "builtin"]

_DOTTED = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")
_ROOT = re.compile(r"[A-Za-z_$][\w$]*")
_PY_SUPER = re.compile(r"super\(.*\)", re.DOTALL)
_PY_LITERAL = re.compile(r"""(?:[rRbBuUfF]{1,2})?["']|[0-9\[{]""")
_JS_LITERAL = re.compile(r"""["'`0-9\[{/]""")
_KEYWORDS = frozenset({"new", "await", "typeof", "void", "yield", "delete", "not", "lambda"})


@dataclass(frozen=True, slots=True)
class CallResolution:
    outcome: CallOutcome
    tier: Tier | None = None
    targets: tuple[str, ...] = ()  # one edge each; constructors add the class's __init__
    candidates: int = 1  # how many definers a T5/T6 match found
    reason: UnresolvedReason | None = None
    unresolved_candidates: tuple[str, ...] = ()


EXTERNAL = CallResolution("external")
BUILTIN = CallResolution("builtin")


def _unresolved(reason: UnresolvedReason, candidates: Sequence[str] = ()) -> CallResolution:
    kept = tuple(candidates[:MAX_UNRESOLVED_CANDIDATES])
    return CallResolution("unresolved", reason=reason, unresolved_candidates=kept)


class CallResolver:
    def __init__(self, names: NameResolver, hierarchy: ClassHierarchy) -> None:
        self.names = names
        self.index: SymbolIndex = names.index
        self.hierarchy = hierarchy

    def resolve(self, path: str, call: CallSite) -> CallResolution:
        language = self.index.language.get(path, "py")
        python = language == "py"
        name, receiver, enclosing = call.name, call.receiver, call.enclosing
        if name is None or (not python and name == "this"):
            return _unresolved("dynamic")
        if receiver is None:
            if not python and name == "super":
                return self._super_constructor(enclosing)
            resolved = self.names.resolve_name(path, name, enclosing)
            if resolved is None:
                found = self.index.module_candidates(language, name)
                return _unresolved("unbound_name", [symbol.key for symbol in found])
            return self._bound(resolved.target, Tier.T1, language)

        hit: CallResolution | None = None
        if receiver in (("self", "cls") if python else ("this",)):
            hit = self._on_self(path, enclosing, name, language)
        elif (python and _PY_SUPER.fullmatch(receiver)) or (not python and receiver == "super"):
            hit = self._on_super(enclosing, name, language)
        elif (_PY_LITERAL if python else _JS_LITERAL).match(receiver):
            return BUILTIN
        elif _DOTTED.fullmatch(receiver):
            hit = self._on_dotted(path, receiver, name, enclosing, language)
        else:
            root = _ROOT.match(receiver)
            if root is not None and root.group(0) not in _KEYWORDS:
                bound = self.names.resolve_name(path, root.group(0), enclosing)
                kind = bound.target.kind if bound is not None else None
                if kind in ("external", "missing"):
                    return EXTERNAL
                if kind == "builtin":
                    return BUILTIN
        return hit if hit is not None else self._by_name(path, language, name)

    # -- receivers -------------------------------------------------------------------------

    def _on_self(
        self, path: str, enclosing: str | None, name: str, language: str
    ) -> CallResolution | None:
        cls = self.index.enclosing_class(enclosing)
        if cls is not None:
            found = self.hierarchy.find(cls.key, name)
            if found is not None:
                return self._resolved(Tier.T3, [found.key], language)
            return EXTERNAL if self.hierarchy.has_unresolved_bases(cls.key) else None
        if language != "py":  # `this` in an object-literal method is the object: api.get → api.m
            for key in self.index.scope_chain(enclosing):
                symbol = self.index.symbols[key]
                if symbol.parent is None and "." in symbol.qualname:
                    owner = symbol.qualname.rsplit(".", 1)[0]
                    sibling = self.index.by_qualname(path, f"{owner}.{name}")
                    if sibling is not None and sibling.parent is None:
                        return self._resolved(Tier.T3, [sibling.key], language)
                    break
        return None

    def _on_super(self, enclosing: str | None, name: str, language: str) -> CallResolution | None:
        cls = self.index.enclosing_class(enclosing)
        if cls is None:
            return None
        found = self.hierarchy.find(cls.key, name, skip_self=True)
        if found is not None:
            return self._resolved(Tier.T4, [found.key], language)
        if self.hierarchy.has_unresolved_bases(cls.key):
            return EXTERNAL
        return _unresolved("not_found")  # super() only reaches the bases: no guessing

    def _super_constructor(self, enclosing: str | None) -> CallResolution:
        cls = self.index.enclosing_class(enclosing)
        if cls is None:
            return _unresolved("not_found")
        found = self.hierarchy.find(cls.key, "constructor", skip_self=True)
        if found is not None:
            return CallResolution("resolved", Tier.T4, (found.key,))
        bases = self.hierarchy.bases.get(cls.key)
        if bases:  # the base has no explicit constructor: the call reaches the class itself
            return CallResolution("resolved", Tier.T4, (bases[0].base,))
        return (
            EXTERNAL if self.hierarchy.has_unresolved_bases(cls.key) else _unresolved("not_found")
        )

    def _on_dotted(
        self, path: str, receiver: str, name: str, enclosing: str | None, language: str
    ) -> CallResolution | None:
        parts = receiver.split(".")
        root = self.names.resolve_name(path, parts[0], enclosing)
        same_file = self.index.by_qualname(path, f"{receiver}.{name}")
        if same_file is not None and (root is None or root.via == "definition"):
            return self._bound(Target.for_symbol(same_file.key, path), Tier.T1, language)
        if root is None:
            return None
        target: Target | None = root.target
        for part in parts[1:]:
            target = self.names.member_of(target, part) if target is not None else None
        return self._on_object(target, name, language) if target is not None else None

    def _on_object(self, target: Target, name: str, language: str) -> CallResolution | None:
        if target.kind in ("external", "missing"):
            return EXTERNAL
        if target.kind == "builtin":
            return BUILTIN
        if target.kind == "module" and target.module is not None:
            # The receiver is known; a member that is a value (a factory-made function, say)
            # stays unresolved rather than being guessed by name.
            return self._bound(self.names.resolvers.member(target.module, name), Tier.T2, language)
        if target.kind == "symbol" and target.key is not None:
            if self.index.kind(target.key) != "class":
                return None  # the receiver is what a function returns: unknown, match by name
            member = self.names.class_member(target.key, name)
            if member is None:
                return _unresolved("not_found")
            return self._bound(member, Tier.T2, language)
        # A module-level value (an object literal, or an instance of something): its own
        # functions if it is a literal, otherwise match by name.
        literal = self.names.member_of(target, name)
        return self._bound(literal, Tier.T2, language) if literal is not None else None

    # -- results ---------------------------------------------------------------------------

    def _bound(self, target: Target, tier: Tier, language: str) -> CallResolution:
        if target.kind == "symbol" and target.key is not None:
            return self._resolved(tier, [target.key], language)
        if target.kind == "module" and target.module is not None:
            called = self.names.resolvers.called_module(target.module)
            if called is not None and called.kind == "symbol" and called.key is not None:
                return self._resolved(tier, [called.key], language)
            if called is not None and called.kind == "external":
                return EXTERNAL
            return _unresolved("not_found")
        if target.kind in ("external", "missing"):
            return EXTERNAL
        if target.kind == "builtin":
            return BUILTIN
        return _unresolved("not_found")  # bound to a value that is not a function or class

    def _by_name(self, path: str, language: str, name: str) -> CallResolution:
        found = self.index.member_candidates(language, name, path)
        if not found:
            return _unresolved("not_found")
        if len(found) > MAX_CANDIDATES:
            return _unresolved("ambiguous", [symbol.key for symbol in found])
        tier = Tier.T5 if len(found) == 1 else Tier.T6
        return self._resolved(tier, [symbol.key for symbol in found], language, len(found))

    def _resolved(
        self, tier: Tier, keys: list[str], language: str, candidates: int = 1
    ) -> CallResolution:
        constructor = "__init__" if language == "py" else "constructor"
        targets: list[str] = []
        for key in keys:
            targets.append(key)
            if self.index.kind(key) == "class":
                init = self.hierarchy.find(key, constructor)
                if init is not None:
                    targets.append(init.key)
        unique = tuple(dict.fromkeys(targets))
        return CallResolution("resolved", tier, unique, candidates)


CallerOf = Callable[[ParsedFile, CallSite], str]


def add_call_edges(
    files: Sequence[ParsedFile],
    resolver: CallResolver,
    edges: EdgeSet,
    caller_of: CallerOf,
) -> tuple[list[UnresolvedCall], Counter[str]]:
    """``calls`` edges for every call site, plus unresolved calls and outcome counts.

    Counts use the keys ``T1``…``T6`` for resolved sites, ``unresolved:<reason>``,
    ``external``, and ``builtin``.
    """
    unresolved: list[UnresolvedCall] = []
    counts: Counter[str] = Counter()
    for parsed in files:
        for call in parsed.calls:
            caller = caller_of(parsed, call)
            result = resolver.resolve(parsed.path, call)
            if result.outcome == "resolved" and result.tier is not None:
                counts[result.tier.value] += 1
                confidence = tier_confidence(result.tier, result.candidates)
                meta: dict[str, JsonValue] = {"tier": result.tier.value}
                if result.tier in (Tier.T5, Tier.T6):
                    meta["candidates"] = result.candidates
                for target in result.targets:
                    edges.add(
                        caller,
                        target,
                        EdgeKind.CALLS,
                        Provenance.NAME_MATCH,
                        confidence,
                        site=call.range,
                        meta=meta,
                        counted=True,
                    )
            elif result.outcome == "unresolved" and result.reason is not None:
                counts[f"unresolved:{result.reason}"] += 1
                unresolved.append(
                    UnresolvedCall(
                        caller_key=caller,
                        callee_text=call.callee,
                        receiver_text=call.receiver,
                        site=call.range,
                        candidates=list(result.unresolved_candidates),
                        reason=result.reason,
                    )
                )
            else:
                counts[result.outcome] += 1
    return unresolved, counts
