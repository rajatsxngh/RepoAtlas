"""What SCIP says about one repo, keyed by symbol keys (PRD §10.2).

Relationships come only from the SCIP index. ``parse_file`` is used for one thing: turning a
location into a symbol key (a SCIP definition's name line into the key of the symbol defined
there, and, where SCIP gives a reference no enclosing function, a reference's line into the key
of the parsed function, method, class, or test case around it).

**Caller of X:** the innermost enclosing definition of a non-import reference to X.

- The enclosing definition is found by position containment in SCIP's enclosing ranges. If the
  innermost one is not a function, method, class, or test (a variable, a property), the next
  one out is used.
- When no SCIP definition with an enclosing range is a function, method, class, or test, or a
  parsed one lies strictly inside SCIP's innermost scope, the caller is the innermost parsed
  one whose span contains the reference. SCIP gives no definition to property setters and
  ``@overload`` stubs (scip-python lists them as references to the first definition), and
  scip-typescript gives no enclosing range to test callbacks (``it("…", () => …)``) or to
  object-literal methods in ``const X = make(…, { m() { … } })`` (zod's style). The reference
  itself is always SCIP's.
- References inside an ``import`` statement are not calls. The indexers do not set SCIP's
  import role, so import statements are found in the source: Python's ``ast`` and, for
  TypeScript, the lines of top-level ``import``/``export … from`` statements.
- Module-level references have no enclosing definition and so no caller.
- A "reference" at the name of another definition (scip-python lists the second and later
  ``@overload`` or conditional definitions of a name that way) is a redefinition, not a call.
- A reference to a class followed by ``(`` (a constructor call, ``new C(`` in TypeScript) also
  counts as a call to the class's constructor (``__init__`` or ``constructor``).
- A reference whose text does not name its target (not the target's name, its class's name
  for a constructor, or a local import alias) is an indexer mis-resolution: it is ignored and
  counted in ``misresolved``. scip-python 0.6.8 resolves many ``httpx.<Name>`` references in
  httpx's tests to a neighbouring name in the package's export list.
- Overrides: callers of a method that SCIP links to a base method through an
  ``is_implementation`` relationship are kept separately (``override_callers``) so questions can
  list them in ``notes`` without mixing them into the answer.
"""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
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
    # parsed callables SCIP has no definition for (property setters, @overload stubs) that are
    # the innermost enclosing definition of some reference
    redefinitions: dict[str, Symbol] = field(default_factory=dict)
    # TypeScript definitions whose name is also an interface or type in the same file: SCIP
    # merges the declarations, so type-only uses count as references to them
    type_merged: set[str] = field(default_factory=set)
    # (callee, caller) → 1-based (path, line) of each reference that made the caller a caller
    call_sites: dict[tuple[str, str], list[tuple[str, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    unmatched_definitions: int = 0
    # (text written at the reference, key SCIP resolved it to) for references whose text does
    # not name the target: indexer mis-resolutions, ignored and counted
    misresolved: Counter[tuple[str, str]] = field(default_factory=Counter)

    def tainted_names(self) -> set[str]:
        """Names on either side of a mis-resolved reference (the text written there, and the
        target SCIP chose, with its class): code typed through them may be mis-resolved too."""
        names = set()
        for text, key in self.misresolved:
            names.add(text)
            found = self.definitions.get(key)
            if found is not None:
                names.add(found.name)
                names.add(found.qualname.split(".")[0])
        return names

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
        test = self.test_symbols.get(key) or self.redefinitions[key]
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


def _parsed_owner(parsed: ParsedFile, line: int) -> Symbol | None:
    """The innermost parsed function, method, class, or test whose span contains ``line``."""
    found = [
        s
        for s in parsed.symbols
        if s.kind in CALLABLE_KINDS and s.span.start_line <= line <= s.span.end_line
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
            if not document.path.endswith((".py", ".pyi")) and _declares_type(
                files.text(document.path), symbol.name
            ):
                facts.type_merged.add(symbol.key)

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
            if occ.range.start_line == occ.range.end_line:
                written = lines[occ.range.start_line][occ.range.start_char : occ.range.end_char]
                if not _names_target(written, target, text, facts):
                    facts.misresolved[(written, target.key)] += 1
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


def _declares_type(text: str, name: str) -> bool:
    kinds = r"(?m)^\s*(?:export\s+)?(?:declare\s+)?(?:interface|type)\s+"
    pattern = kinds + re.escape(name) + r"(?![\w$])"
    return re.search(pattern, text) is not None


def _names_target(written: str, target: Definition, text: str, facts: Facts) -> bool:
    """Whether the text at a reference plausibly names ``target``: its own name (possibly
    quoted, or the whole ``name as alias`` span of an import), its class's name for a
    constructor (``new X(``), or a local alias (``import X as Y``, ``{ X as Y }``, or a default
    import ``import Y from``)."""
    name = re.escape(target.name)
    if written.strip("'\"`") == target.name or re.fullmatch(rf"{name}\s+as\s+\w+", written):
        return True
    if target.name in CONSTRUCTORS and target.qualname.rsplit(".", 1)[0].endswith(written):
        return True
    alias = rf"(?<!\w){name}\s+as\s+{re.escape(written)}(?!\w)"
    default = rf"(?m)^\s*import\s+(?:type\s+)?{re.escape(written)}\s*(?:,|from\b)"
    return re.search(alias, text) is not None or re.search(default, text) is not None


def _caller(index: ScipIndex, occ: Occurrence, facts: Facts, file: ParsedFile) -> str | None:
    """The innermost enclosing definition: SCIP's innermost callable scope, unless a parsed
    callable lies strictly inside it (or SCIP has none), which SCIP gives no definition of."""
    scope: Occurrence | None = None
    key: str | None = None
    for candidate in index.enclosing_definitions(occ):
        found = facts.by_scip.get(candidate.symbol)
        if found is not None and found.kind in CALLABLE_KINDS:
            scope, key = candidate, found.key
            break
    owner = _parsed_owner(file, occ.line)
    if owner is None or owner.key == key:
        return key
    if scope is not None and scope.enclosing_range is not None:
        first, last = scope.enclosing_range.start_line + 1, scope.enclosing_range.end_line + 1
        inside = first <= owner.span.start_line and owner.span.end_line <= last
        if not inside or (owner.span.start_line, owner.span.end_line) == (first, last):
            return key
    if owner.key not in facts.definitions and owner.key not in facts.test_symbols:
        facts.redefinitions[owner.key] = owner
    return owner.key
