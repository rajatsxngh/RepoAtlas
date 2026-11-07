"""Per-file parse results (ING-7..11). Positions: 1-based lines, 0-based UTF-8 byte columns."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from repoatlas.ingest.inventory import Language
from repoatlas.parsing.symbols import SymbolKind


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Range(_Model):
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class Symbol(_Model):
    key: str
    kind: SymbolKind
    name: str
    qualname: str
    parent: str | None
    name_range: Range
    span: Range
    signature: str
    decorators: list[str] = []
    # async, generator, static, getter, setter, property, classmethod, staticmethod
    modifiers: list[str] = []
    exported: bool


class ImportBinding(_Model):
    kind: Literal["module", "named", "default", "namespace", "star"]
    name: str | None
    local: str | None


class ImportStmt(_Model):
    kind: Literal["static", "dynamic", "require", "reexport"]
    module: str
    level: int = 0
    bindings: list[ImportBinding] = []
    type_only: bool = False
    scope: str | None
    range: Range


class CallSite(_Model):
    callee: str
    name: str | None
    receiver: str | None
    kind: Literal["call", "new"]
    enclosing: str | None
    range: Range


class ClassBase(_Model):
    class_key: str
    text: str
    relation: Literal["extends", "implements"]
    range: Range


class RouteCandidate(_Model):
    source: Literal["decorator", "call"]
    methods: list[str]
    path: str
    handler: str | None
    text: str
    range: Range


class TestCase(_Model):
    __test__: ClassVar[bool] = False  # not a pytest test class

    key: str
    framework: Literal["pytest", "jest"]
    title_path: list[str]
    runner_id: str
    range: Range


class Export(_Model):
    name: str
    local: str | None
    range: Range


class ParseIssue(_Model):
    kind: Literal["syntax_error", "missing_node", "decode_error"]
    message: str
    range: Range | None


class ParsedFile(_Model):
    path: str
    language: Language
    blob_oid: str
    symbols: list[Symbol] = []
    imports: list[ImportStmt] = []
    calls: list[CallSite] = []
    class_bases: list[ClassBase] = []
    routes: list[RouteCandidate] = []
    tests: list[TestCase] = []
    exports: list[Export] = []
    issues: list[ParseIssue] = []
    issue_count: int = 0
