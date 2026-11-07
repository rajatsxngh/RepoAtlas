"""What an imported name refers to, as decided by the module resolvers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TargetKind = Literal["module", "symbol", "member", "external", "missing", "builtin"]
ImportOutcome = Literal["internal", "external", "missing"]


@dataclass(frozen=True, slots=True)
class ModuleRef:
    """A module in the repository: a file, a package (``__init__.py`` plus its directory), or a
    Python namespace package (a directory without ``__init__.py``, so ``file`` is ``None``)."""

    file: str | None
    package: str | None = None  # the package directory, for packages and namespace packages

    @property
    def label(self) -> str:
        return self.file or f"{self.package}/"


@dataclass(frozen=True, slots=True)
class Target:
    """The thing a local name is bound to.

    - ``module``: a module in the repository (``module`` is set).
    - ``symbol``: a parsed symbol (``key`` and ``path`` are set).
    - ``member``: a name inside a repository module that is not a symbol, such as a variable
      (``path`` is the module file, ``name`` the member).
    - ``external``: outside the analyzed code — a package, the standard library, or a file the
      inventory skipped (``name`` says which).
    - ``missing``: looks like repository code (a relative import, say) but nothing is there.
    - ``builtin``: a language builtin or runtime global (``len``, ``console``).
    """

    kind: TargetKind
    path: str | None = None
    key: str | None = None
    name: str | None = None
    module: ModuleRef | None = None

    @classmethod
    def for_module(cls, module: ModuleRef) -> Target:
        return cls("module", path=module.file, module=module)

    @classmethod
    def for_symbol(cls, key: str, path: str) -> Target:
        return cls("symbol", path=path, key=key)

    @classmethod
    def for_member(cls, path: str, name: str) -> Target:
        return cls("member", path=path, name=name)

    @classmethod
    def for_external(cls, name: str) -> Target:
        return cls("external", name=name)

    @classmethod
    def for_missing(cls, name: str) -> Target:
        return cls("missing", name=name)

    @classmethod
    def for_builtin(cls, name: str) -> Target:
        return cls("builtin", name=name)


@dataclass(frozen=True, slots=True)
class ResolvedImport:
    """One import statement, resolved.

    ``files`` are the repository files it imports (one ``imports`` edge each), ``bindings`` the
    local names it creates, and ``stars`` the modules whose public names it binds (``from m
    import *``). ``outcome`` is ``internal`` when it reaches repository code.
    """

    outcome: ImportOutcome
    files: tuple[str, ...] = ()
    bindings: tuple[tuple[str, Target], ...] = ()
    stars: tuple[ModuleRef, ...] = field(default=())
