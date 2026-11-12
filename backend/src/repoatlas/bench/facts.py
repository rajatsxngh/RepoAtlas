"""What SCIP says about one repo, keyed by symbol keys (PRD §10.2).

Relationships come only from the SCIP index. ``parse_file`` is used for one thing: turning a
location into a symbol key (a SCIP definition's name line into the key of the symbol defined
there, and, where SCIP gives a reference no enclosing function, a reference's line into the key
of the parsed function, method, class, or test case around it).

**Caller of X:** the innermost enclosing definition of a non-import reference to X.

- The enclosing definition is found by position containment in SCIP's enclosing ranges. If the
  innermost one is not a function, method, class, or test (a variable, a property), the next
  one out is used.
- When no SCIP definition with an enclosing range is a function, method, class, or test, the
  caller is the innermost parsed one whose span contains the reference. scip-typescript gives
  no enclosing range to test callbacks (``it("…", () => …)``) or to object-literal methods in
  ``const X = make(…, { m() { … } })`` (zod's style); the reference itself is still SCIP's.
- References inside an ``import`` statement are not calls. The indexers do not set SCIP's
  import role, so import statements are found in the source: Python's ``ast`` and, for
  TypeScript, the lines of top-level ``import``/``export … from`` statements.
- Module-level references have no enclosing definition and so no caller.
- A "reference" at the name of another definition (scip-python lists the second and later
  ``@overload`` or conditional definitions of a name that way) is a redefinition, not a call.
- A reference to a class followed by ``(`` (a constructor call, ``new C(`` in TypeScript) also
  counts as a call to the class's constructor (``__init__`` or ``constructor``).
- Overrides: callers of a method that SCIP links to a base method through an
  ``is_implementation`` relationship are kept separately (``override_callers``) so questions can
  list them in ``notes`` without mixing them into the answer.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from repoatlas.bench.repos import RepoFiles
from repoatlas.bench.schema import Location
from repoatlas.bench.scip import Occurrence, ScipIndex
from repoatlas.ingest.inventory import is_test_path
from repoatlas.parsing.models import ParsedFile, Symbol
from repoatlas.parsing.symbols import parse_symbol_key

CALLABLE_KINDS = frozenset({"function", "method", "class", "test"})
CONSTRUCTORS = ("__init__", "constructor")


@dataclass(frozen=True, slots=True, order=True)
class Definition:
    key: str
    path: str
    line: int  # 1-based line of the name
    name: str
    qualname: str
    kind: str
    scip_symbol: str
    is_test_file: bool


@dataclass
class Facts:
    repo_id: str
    commit: str
    definitions: dict[str, Definition] = field(default_factory=dict)  # by symbol key
    by_scip: dict[str, Definition] = field(default_factory=dict)  # by SCIP symbol
    callers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    override_callers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    overrides: dict[str, list[str]] = field(default_factory=dict)  # base key → override keys
    # file → keys of definitions in other files it references (imports included)
    file_references: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    test_symbols: dict[str, Symbol] = field(default_factory=dict)  # parsed test cases by key
    # (callee, caller) → 1-based (path, line) of each reference that made the caller a caller
    call_sites: dict[tuple[str, str], list[tuple[str, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    unmatched_definitions: int = 0

    def callees(self) -> dict[str, set[str]]:
        """caller key → keys it calls (the inverse of ``callers``)."""
        out: dict[str, set[str]] = defaultdict(set)
        for callee, callers in self.callers.items():
            for caller in callers:
                out[caller].add(callee)
        return out

    def location(self, key: str) -> Location:
        """Where ``key`` is defined: its SCIP definition, or the parsed test case's title."""
        found = self.definitions.get(key)
        if found is not None:
            return Location(symbol_key=key, path=found.path, line=found.line)
        test = self.test_symbols[key]
        path = parse_symbol_key(key).path
        return Location(symbol_key=key, path=path, line=test.name_range.start_line)

    def is_test(self, key: str) -> bool:
        found = self.definitions.get(key)
        if found is not None:
            return found.is_test_file
        return key in self.test_symbols


# --------------------------------------------------------------------------------------------
# Import statements in the source
# --------------------------------------------------------------------------------------------

_TS_IMPORT_START = re.compile(r"^\s*(import\b|export\s+(\*|\{|type\s*\{))")
_TS_STATEMENT_END = re.compile(r"""(from\s*['"][^'"]*['"]|^\s*import\s*['"][^'"]*['"])\s*;?\s*$""")


def import_lines(path: str, text: str) -> set[int]:
    """1-based lines that belong to import statements (and ``export … from`` re-exports)."""
    lines: set[int] = set()
    if path.endswith((".py", ".pyi")):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return lines
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        return lines
    source = text.splitlines()
    index = 0
    while index < len(source):
        if _TS_IMPORT_START.match(source[index]):
            start = index
            while index < len(source) and not _TS_STATEMENT_END.search(source[index]):
                index += 1
                if index - start > 200:  # not an import after all
                    index = start
                    break
            if index > start or _TS_STATEMENT_END.search(source[start]):
                lines.update(range(start + 1, index + 2))
        index += 1
    return lines


# --------------------------------------------------------------------------------------------
# Building the facts
# --------------------------------------------------------------------------------------------


def _parsed_owner(parsed: ParsedFile, line: int, known: Callable[[str], bool]) -> Symbol | None:
    """The innermost parsed function, method, class, or test whose span contains ``line`` and
    whose location is known (a SCIP definition or a test case; not an ``@overload`` stub)."""
    found = [
        s
        for s in parsed.symbols
        if s.kind in CALLABLE_KINDS
        and s.span.start_line <= line <= s.span.end_line
        and known(s.key)
    ]
    return min(found, key=lambda s: (s.span.end_line - s.span.start_line, s.key), default=None)


def _match(symbols_on_line: list[Symbol], occ: Occurrence, files: RepoFiles) -> Symbol | None:
    text = files.line(occ.path, occ.line) or ""
    name = text[occ.range.start_char : occ.range.end_char]
    matches = [s for s in symbols_on_line if s.name == name and s.kind in CALLABLE_KINDS]
    return min(matches, key=lambda s: s.key, default=None)


def build_facts(index: ScipIndex, parsed: Mapping[str, ParsedFile], files: RepoFiles) -> Facts:
    facts = Facts(repo_id=files.repo_id, commit=files.commit)
    documents = [d for d in index.documents() if d.path in parsed]
    for file in parsed.values():
        for symbol in file.symbols:
            if symbol.kind == "test":
                facts.test_symbols[symbol.key] = symbol

    # Pass 1: SCIP definitions → symbol keys (by name line).
    for document in documents:
        file = parsed[document.path]
        by_line: dict[int, list[Symbol]] = defaultdict(list)
        for symbol in file.symbols:
            by_line[symbol.name_range.start_line].append(symbol)
        is_test = is_test_path(document.path, file.language)
        for occ in document.occurrences:
            if not occ.is_definition or occ.is_local or occ.symbol in facts.by_scip:
                continue
            matched = _match(by_line.get(occ.line, []), occ, files)
            if matched is None:
                if occ.enclosing_range is not None:
                    facts.unmatched_definitions += 1
                continue
            if matched.key in facts.definitions:
                continue
            symbol = matched
            definition = Definition(
                key=symbol.key,
                path=document.path,
                line=occ.line,
                name=symbol.name,
                qualname=symbol.qualname,
                kind=symbol.kind,
                scip_symbol=occ.symbol,
                is_test_file=is_test,
            )
            facts.definitions[symbol.key] = definition
            facts.by_scip[occ.symbol] = definition

    classes = {(d.path, d.qualname): d.key for d in facts.definitions.values() if d.kind == "class"}
    constructors: dict[str, str] = {}  # class key → constructor key
    for definition in facts.definitions.values():
        if definition.kind == "method" and definition.name in CONSTRUCTORS:
            owner = classes.get((definition.path, definition.qualname.rsplit(".", 1)[0]))
            if owner is not None:
                constructors[owner] = definition.key

    # Pass 2: references → callers.
    direct: dict[str, set[str]] = defaultdict(set)
    for document in documents:
        file = parsed[document.path]
        text = files.text(document.path)
        imports = import_lines(document.path, text)
        lines = files.lines(document.path)
        name_sites = {(s.name_range.start_line, s.name_range.start_col) for s in file.symbols}
        for occ in document.occurrences:
            if occ.is_definition or occ.is_local:
                continue
            if (occ.line, occ.range.start_char) in name_sites:
                continue  # a redefinition (overload, conditional def) listed as a reference
            target = facts.by_scip.get(occ.symbol)
            if target is None:
                continue
            if target.path != document.path:
                facts.file_references[document.path].add(target.key)
            if occ.is_import or occ.line in imports:
                continue
            caller = _caller(index, occ, facts, file)
            if caller is None or caller == target.key:
                continue
            direct[target.key].add(caller)
            facts.call_sites[(target.key, caller)].append((document.path, occ.line))
            if target.kind == "class" and target.key in constructors:
                after = lines[occ.range.end_line][occ.range.end_char :] if lines else ""
                if after.lstrip().startswith("("):
                    direct[constructors[target.key]].add(caller)
                    site = (constructors[target.key], caller)
                    facts.call_sites[site].append((document.path, occ.line))

    # Overrides: SCIP implementation relationships, base method → overriding methods.
    for definition in facts.definitions.values():
        if definition.kind != "method":
            continue
        overrides = [
            facts.by_scip[s].key
            for s in index.implementations(definition.scip_symbol)
            if s in facts.by_scip
        ]
        if overrides:
            facts.overrides[definition.key] = sorted(overrides)
    for key, callers in direct.items():
        facts.callers[key] = callers
    for base, overrides in facts.overrides.items():
        for override in overrides:
            facts.override_callers[base] |= direct.get(override, set())
    return facts


def _caller(index: ScipIndex, occ: Occurrence, facts: Facts, file: ParsedFile) -> str | None:
    for scope in index.enclosing_definitions(occ):
        found = facts.by_scip.get(scope.symbol)
        if found is not None and found.kind in CALLABLE_KINDS:
            return found.key
    owner = _parsed_owner(
        file, occ.line, lambda k: k in facts.definitions or k in facts.test_symbols
    )
    return owner.key if owner is not None else None
