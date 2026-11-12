"""Mutation targets from SCIP (SBX-8): functions that tests reach, stratified by fan-in.

The SCIP index decides what references what, so sampling does not depend on RepoAtlas's own
graph; ``parse_file`` only turns SCIP locations into symbol keys and spans.

1. Every SCIP definition is matched to the parsed symbol defined on the same line with that
   name.
2. Every reference is attributed to the innermost parsed symbol whose span contains it.
3. Functions and methods in non-test files that a test file references directly are at hop 1;
   functions those reference are at hop 2, and so on up to ``MAX_HOPS``.
4. Fan-in is the number of distinct definitions that reference a function. Reached functions
   that pass the mutation skip rules are split into fan-in quartiles. Each quartile is shuffled
   with the seed and the quartiles are interleaved, so a larger ``n`` extends a smaller sample.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from repoatlas.ingest.inventory import is_test_path
from repoatlas.parsing.models import ParsedFile, Symbol
from repoatlas.sandbox.mutate import skip_reason
from repoatlas.sandbox.scip import ScipIndex

SymbolKey = str
MAX_HOPS = 3
STRATA = 4
_DEFINITION_KINDS = frozenset({"function", "method", "class", "test"})


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: SymbolKey
    fan_in: int
    hops: int
    stratum: int = 0


class ReferenceGraph(BaseModel):
    """What SCIP says, keyed by symbol keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    references: dict[SymbolKey, list[SymbolKey]]  # referencing definition → referenced keys
    from_tests: list[SymbolKey]  # keys referenced anywhere in a test file
    unmatched_definitions: int  # SCIP definitions with no parsed symbol on that line


def _innermost_by_line(parsed: ParsedFile) -> dict[int, str]:
    """1-based line → key of the innermost definition covering it."""
    owners: dict[int, str] = {}
    spans = [s for s in parsed.symbols if s.kind in _DEFINITION_KINDS]
    # Paint larger spans first so nested definitions overwrite their parents.
    for symbol in sorted(spans, key=lambda s: s.span.start_line - s.span.end_line):
        for line in range(symbol.span.start_line, symbol.span.end_line + 1):
            owners[line] = symbol.key
    return owners


def _definitions_by_line(parsed: ParsedFile) -> dict[int, list[Symbol]]:
    by_line: dict[int, list[Symbol]] = defaultdict(list)
    for symbol in parsed.symbols:
        by_line[symbol.name_range.start_line].append(symbol)
    return by_line


def _is_test_file(parsed: ParsedFile) -> bool:
    return is_test_path(parsed.path, parsed.language)


def reference_graph(index: ScipIndex, parsed: Mapping[str, ParsedFile]) -> ReferenceGraph:
    prefix = index.path_prefix()
    documents = [(prefix + d.relative_path, d) for d in index.documents]
    # Pass 1: SCIP symbol → our key, from definitions.
    scip_to_key: dict[str, str] = {}
    unmatched = 0
    for path, document in documents:
        file = parsed.get(path)
        if file is None:
            continue
        by_line = _definitions_by_line(file)
        for occ in document.occurrences:
            if not occ.is_definition or occ.is_local or occ.symbol in scip_to_key:
                continue
            matches = [s for s in by_line.get(occ.start_line + 1, []) if s.name in occ.symbol]
            if matches:
                scip_to_key[occ.symbol] = matches[0].key
            else:
                unmatched += 1
    # Pass 2: references, attributed to the innermost enclosing definition.
    references: dict[str, set[str]] = defaultdict(set)
    from_tests: set[str] = set()
    for path, document in documents:
        file = parsed.get(path)
        if file is None:
            continue
        owners = _innermost_by_line(file)
        in_test = _is_test_file(file)
        for occ in document.occurrences:
            if occ.is_definition or occ.is_local:
                continue
            target = scip_to_key.get(occ.symbol)
            if target is None:
                continue
            if in_test:
                from_tests.add(target)
            owner = owners.get(occ.start_line + 1)
            if owner is not None and owner != target:
                references[owner].add(target)
    return ReferenceGraph(
        references={k: sorted(v) for k, v in sorted(references.items())},
        from_tests=sorted(from_tests),
        unmatched_definitions=unmatched,
    )


def candidates(index: ScipIndex, parsed: Mapping[str, ParsedFile]) -> list[Candidate]:
    """Every mutable function that tests reach within ``MAX_HOPS``, with fan-in and hops."""
    graph = reference_graph(index, parsed)
    symbols = {s.key: (s, file) for file in parsed.values() for s in file.symbols}

    def mutable(key: str) -> bool:
        found = symbols.get(key)
        if found is None:
            return False
        symbol, file = found
        return not _is_test_file(file) and skip_reason(symbol) is None

    def is_code(key: str) -> bool:
        found = symbols.get(key)
        return found is not None and not _is_test_file(found[1])

    hops: dict[str, int] = {}
    frontier = sorted(k for k in graph.from_tests if is_code(k))
    for depth in range(1, MAX_HOPS + 1):
        next_frontier: set[str] = set()
        for key in frontier:
            if key in hops:
                continue
            hops[key] = depth
            next_frontier.update(t for t in graph.references.get(key, []) if is_code(t))
        frontier = sorted(next_frontier - set(hops))

    fan_in: dict[str, set[str]] = defaultdict(set)
    for source, targets in graph.references.items():
        for target in targets:
            fan_in[target].add(source)
    return sorted(
        (
            Candidate(key=key, fan_in=len(fan_in.get(key, ())), hops=depth)
            for key, depth in hops.items()
            if mutable(key)
        ),
        key=lambda c: (c.fan_in, c.key),
    )


def stratify(found: list[Candidate], strata: int = STRATA) -> list[list[Candidate]]:
    """Contiguous fan-in quartiles of the (fan-in, key)-sorted candidates."""
    ordered = sorted(found, key=lambda c: (c.fan_in, c.key))
    groups: list[list[Candidate]] = []
    for index in range(strata):
        start = len(ordered) * index // strata
        end = len(ordered) * (index + 1) // strata
        groups.append([c.model_copy(update={"stratum": index}) for c in ordered[start:end]])
    return groups


def ordered_sample(found: list[Candidate], seed: int) -> list[Candidate]:
    """All candidates in sampling order: each stratum shuffled, strata interleaved."""
    groups = stratify(found)
    for index, group in enumerate(groups):
        random.Random(f"{seed}:{index}").shuffle(group)
    order: list[Candidate] = []
    for position in range(max((len(g) for g in groups), default=0)):
        order.extend(group[position] for group in groups if position < len(group))
    return order


def sample_targets(
    scip_index: ScipIndex, parsed: Mapping[str, ParsedFile], n: int, seed: int
) -> list[SymbolKey]:
    """``n`` target keys, stratified by fan-in quartile, reproducible from ``seed``."""
    return [c.key for c in ordered_sample(candidates(scip_index, parsed), seed)[:n]]
