"""SCIP indexes through Python bindings generated from a pinned ``scip.proto`` (BEN-2).

The benchmark's structural ground truth comes only from SCIP, so this module reads every field
it needs from the generated bindings (``repoatlas.bench._proto.scip_pb2``) rather than from
RepoAtlas's own parser or graph. ``_proto/README.md`` names the pinned proto and how to
regenerate the module.

Positions are 0-based, as in SCIP. Paths are repo-relative: an index built from a subproject
(``/src/packages/remeda``) gets that subproject's path as a prefix.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from repoatlas.bench._proto import scip_pb2

ROLE_DEFINITION = 0x1
ROLE_IMPORT = 0x2
CONTAINER_ROOTS = ("/src", "/tmp/work")


class ScipError(ValueError):
    """The bytes are not a readable SCIP index."""


@dataclass(frozen=True, slots=True, order=True)
class Range:
    """A half-open SCIP range: (start_line, start_char) up to (end_line, end_char)."""

    start_line: int
    start_char: int
    end_line: int
    end_char: int

    @classmethod
    def from_proto(cls, values: list[int]) -> Range | None:
        if len(values) == 3:
            return cls(values[0], values[1], values[0], values[2])
        if len(values) == 4:
            return cls(values[0], values[1], values[2], values[3])
        return None

    def contains(self, other: Range) -> bool:
        return (self.start_line, self.start_char) <= (other.start_line, other.start_char) and (
            other.end_line,
            other.end_char,
        ) <= (self.end_line, self.end_char)

    @property
    def size(self) -> tuple[int, int]:
        return (self.end_line - self.start_line, self.end_char - self.start_char)


@dataclass(frozen=True, slots=True, order=True)
class Occurrence:
    path: str  # repo-relative
    range: Range
    symbol: str
    roles: int
    enclosing_range: Range | None = None

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & ROLE_DEFINITION)

    @property
    def is_import(self) -> bool:
        """SCIP's import role. scip-python 0.6.8 and scip-typescript 0.6.2 do not set it, so
        callers also check the source line (``repoatlas.bench.facts``)."""
        return bool(self.roles & ROLE_IMPORT)

    @property
    def is_local(self) -> bool:
        return self.symbol.startswith("local ")

    @property
    def line(self) -> int:
        """1-based line of the occurrence's start."""
        return self.range.start_line + 1


@dataclass(frozen=True, slots=True)
class Document:
    path: str
    language: str
    occurrences: tuple[Occurrence, ...]


def path_prefix(project_root: str, container_roots: tuple[str, ...] = CONTAINER_ROOTS) -> str:
    """``packages/x/`` for a project indexed from ``/src/packages/x`` (or its copy under
    ``/tmp/work``); empty for the repo root."""
    root = project_root.removeprefix("file://").rstrip("/")
    for base in container_roots:
        base = base.rstrip("/")
        if root.startswith(base + "/"):
            return root[len(base) + 1 :] + "/"
    return ""


class ScipIndex:
    """Lookups over one index: definitions, references, enclosing definitions, implementations."""

    def __init__(self, index: scip_pb2.Index) -> None:
        self.tool_name = index.metadata.tool_info.name
        self.tool_version = index.metadata.tool_info.version
        self.project_root = index.metadata.project_root
        self.prefix = path_prefix(self.project_root)
        documents: list[Document] = []
        implementations: dict[str, set[str]] = defaultdict(set)
        for proto_doc in index.documents:
            path = self.prefix + proto_doc.relative_path
            occurrences = []
            for proto_occ in proto_doc.occurrences:
                rng = Range.from_proto(list(proto_occ.range))
                if rng is None or not proto_occ.symbol:
                    continue
                occurrences.append(
                    Occurrence(
                        path=path,
                        range=rng,
                        symbol=proto_occ.symbol,
                        roles=proto_occ.symbol_roles,
                        enclosing_range=Range.from_proto(list(proto_occ.enclosing_range)),
                    )
                )
            occurrences.sort()
            documents.append(Document(path, proto_doc.language, tuple(occurrences)))
            for info in proto_doc.symbols:
                for relationship in info.relationships:
                    if relationship.is_implementation:
                        implementations[relationship.symbol].add(info.symbol)
        for info in index.external_symbols:
            for relationship in info.relationships:
                if relationship.is_implementation:
                    implementations[relationship.symbol].add(info.symbol)
        documents.sort(key=lambda d: d.path)
        self._documents = documents
        self._implementations = {k: sorted(v) for k, v in implementations.items()}
        self._definitions: dict[str, list[Occurrence]] = defaultdict(list)
        self._references: dict[str, list[Occurrence]] = defaultdict(list)
        # Per document: definitions with an enclosing range, smallest range first.
        self._scopes: dict[str, list[Occurrence]] = {}
        for document in documents:
            scopes = []
            for occ in document.occurrences:
                if occ.is_local:
                    continue
                if occ.is_definition:
                    self._definitions[occ.symbol].append(occ)
                    if occ.enclosing_range is not None:
                        scopes.append(occ)
                else:
                    self._references[occ.symbol].append(occ)
            scopes.sort(key=lambda o: (o.enclosing_range.size if o.enclosing_range else (0, 0), o))
            self._scopes[document.path] = scopes

    @classmethod
    def from_bytes(cls, data: bytes) -> ScipIndex:
        index = scip_pb2.Index()
        try:
            index.ParseFromString(data)
        except Exception as exc:  # protobuf raises DecodeError, a plain Exception subclass
            raise ScipError(f"not a SCIP index: {exc}") from exc
        return cls(index)

    @classmethod
    def load(cls, path: Path) -> ScipIndex:
        return cls.from_bytes(path.read_bytes())

    def documents(self) -> list[Document]:
        """Every document, sorted by path."""
        return list(self._documents)

    def definitions(self) -> list[Occurrence]:
        """Every non-local definition occurrence, sorted by path and position."""
        return sorted(occ for occs in self._definitions.values() for occ in occs)

    def definition(self, symbol: str) -> Occurrence | None:
        """The first definition of ``symbol`` (by path and position), if the index has one."""
        found = self._definitions.get(symbol)
        return found[0] if found else None

    def references(self, symbol: str) -> list[Occurrence]:
        """Every non-definition occurrence of ``symbol``, sorted by path and position."""
        return list(self._references.get(symbol, ()))

    def enclosing_definition(self, occurrence: Occurrence) -> Occurrence | None:
        """The innermost definition in the same document whose enclosing range contains the
        occurrence (never the occurrence itself)."""
        for scope in self._scopes.get(occurrence.path, ()):
            assert scope.enclosing_range is not None
            if scope != occurrence and scope.enclosing_range.contains(occurrence.range):
                return scope
        return None

    def enclosing_definitions(self, occurrence: Occurrence) -> list[Occurrence]:
        """Every definition whose enclosing range contains the occurrence, innermost first."""
        return [
            scope
            for scope in self._scopes.get(occurrence.path, ())
            if scope != occurrence
            and scope.enclosing_range is not None
            and scope.enclosing_range.contains(occurrence.range)
        ]

    def implementations(self, symbol: str) -> list[str]:
        """Symbols with an ``is_implementation`` relationship to ``symbol`` (its overrides)."""
        return list(self._implementations.get(symbol, ()))

    def symbols(self) -> list[str]:
        """Every non-local symbol with a definition or reference, sorted."""
        return sorted(set(self._definitions) | set(self._references))
