"""Question generators (BEN-3 … BEN-7, BEN-9): deterministic for a given seed.

Every generator takes the repo's ``Facts`` (SCIP relationships keyed by symbol key) and returns
questions numbered from 001 in sampling order. Inputs are always sorted before sampling, and
each generator draws from its own ``random.Random`` seeded with ``"<seed>:<repo>:<type>…"``, so
adding a generator never changes another generator's sample.

Questions come out with ``split = "dev"``; ``repoatlas.bench.split`` assigns the real splits.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from repoatlas.bench.facts import Definition, Facts
from repoatlas.bench.ownership import Ownership
from repoatlas.bench.schema import (
    AnswerSetTruth,
    GroundTruth,
    ImpactTruth,
    Location,
    LookupTruth,
    Provenance,
    Question,
    QuestionType,
    RealFailingTest,
)
from repoatlas.sandbox.mutations import MutationRecord

BENCHMARK_VERSION = "1.0.0"
STRATA = 4
CALLERS_MIN, CALLERS_MAX = 2, 15
MIN_NAME = 3
# Targets come from the library itself, not its examples, docs, or tooling.
NON_LIBRARY_DIRS = ("examples/", "docs/", "doc/", "scripts/", "benchmarks/", "bench/", "website/")


def question(
    facts: Facts,
    qtype: QuestionType,
    n: int,
    text: str,
    truth: GroundTruth,
    provenance: Provenance,
    notes: str = "",
) -> Question:
    return Question(
        id=f"{facts.repo_id}-{qtype}-{n:03d}",
        benchmark_version=BENCHMARK_VERSION,
        repo_id=facts.repo_id,
        commit=facts.commit,
        type=qtype,
        text=text,
        ground_truth=truth,
        split="dev",
        provenance=provenance,
        notes=notes,
    )


def rng(seed: int, *parts: str) -> random.Random:
    return random.Random(":".join([str(seed), *parts]))


def stratified_order[T](
    items: Sequence[T], weight: Callable[[T], int], sort_key: Callable[[T], str], r: random.Random
) -> list[T]:
    """All items in sampling order: split into ``STRATA`` contiguous weight quantiles, shuffle
    each, then interleave, so any prefix is spread across the weight range."""
    ordered = sorted(items, key=lambda item: (weight(item), sort_key(item)))
    groups = [
        ordered[len(ordered) * i // STRATA : len(ordered) * (i + 1) // STRATA]
        for i in range(STRATA)
    ]
    for group in groups:
        r.shuffle(group)
    out: list[T] = []
    for position in range(max((len(g) for g in groups), default=0)):
        out.extend(g[position] for g in groups if position < len(g))
    return out


# --------------------------------------------------------------------------------------------
# Names people would ask about
# --------------------------------------------------------------------------------------------


def _is_library(path: str) -> bool:
    parts = path.split("/")
    return not any("/".join(parts[i:]).startswith(NON_LIBRARY_DIRS) for i in range(len(parts)))


def _askable(definition: Definition) -> bool:
    """Top-level functions and classes, and methods directly in a class, in library code."""
    name = definition.name
    depth = definition.qualname.count(".")
    return (
        not definition.is_test_file
        and _is_library(definition.path)
        and (
            (definition.kind in ("class", "function") and depth == 0)
            or (definition.kind == "method" and depth == 1)
        )
        and len(name) >= MIN_NAME
        and not (name.startswith("__") and name.endswith("__"))
        and "<" not in definition.qualname
    )


def display_names(facts: Facts) -> dict[str, str]:
    """Key → the shortest unambiguous name: the bare name when the repo defines it once,
    otherwise ``Class.method`` when that is unique. Keys with neither are left out."""
    defs = list(facts.definitions.values())
    names = Counter(d.name for d in defs)
    qualnames = Counter(d.qualname for d in defs)
    out: dict[str, str] = {}
    for d in defs:
        if names[d.name] == 1:
            out[d.key] = d.name
        elif qualnames[d.qualname] == 1 and d.qualname.count(".") == 1:
            out[d.key] = d.qualname
    return out


def _callers(facts: Facts, key: str) -> list[str]:
    return sorted(facts.callers.get(key, ()))


# --------------------------------------------------------------------------------------------
# Lookup and callers (BEN-3, BEN-4)
# --------------------------------------------------------------------------------------------


def gen_lookup(facts: Facts, seed: int, count: int = 6) -> list[Question]:
    """Where is `X` defined? for askable definitions, stratified by fan-in."""
    names = display_names(facts)
    pool = [d for d in facts.definitions.values() if _askable(d) and d.key in names]
    order = stratified_order(
        pool,
        lambda d: len(facts.callers.get(d.key, ())),
        lambda d: d.key,
        rng(seed, facts.repo_id, "lookup"),
    )
    out = []
    for n, d in enumerate(order[:count], start=1):
        kind = "class" if d.kind == "class" else "function" if d.kind == "function" else "method"
        text = f"Where is the {kind} `{names[d.key]}` defined?"
        truth = LookupTruth(definition=facts.location(d.key))
        out.append(question(facts, "lookup", n, text, truth, "scip"))
    return out


def gen_callers(facts: Facts, seed: int, count: int = 6) -> list[Question]:
    """Which functions call `X`? for functions and methods with 2 to 15 callers."""
    names = display_names(facts)
    pool = [
        d
        for d in facts.definitions.values()
        if _askable(d)
        and d.kind in ("function", "method")
        and d.key in names
        and CALLERS_MIN <= len(facts.callers.get(d.key, ())) <= CALLERS_MAX
    ]
    order = stratified_order(
        pool,
        lambda d: len(facts.callers[d.key]),
        lambda d: d.key,
        rng(seed, facts.repo_id, "callers"),
    )
    out = []
    for n, d in enumerate(order[:count], start=1):
        text = f"Which functions, methods, or tests call `{names[d.key]}` (defined in `{d.path}`)?"
        answers = [facts.location(k) for k in _callers(facts, d.key)]
        notes = _override_note(facts, d.key)
        truth = AnswerSetTruth(answers=answers)
        out.append(question(facts, "callers", n, text, truth, "scip", notes))
    return out


def _override_note(facts: Facts, key: str) -> str:
    overrides = facts.overrides.get(key)
    if not overrides:
        return ""
    extra = sorted(facts.override_callers.get(key, set()) - facts.callers.get(key, set()))
    note = "Overridden by " + ", ".join(overrides) + "."
    if extra:
        note += " Callers of the overrides (not in the answer): " + ", ".join(extra) + "."
    return note


# --------------------------------------------------------------------------------------------
# Multi-hop (BEN-5)
# --------------------------------------------------------------------------------------------

REACH_MIN, REACH_MAX = 2, 20
MODULES_MIN, MODULES_MAX = 2, 15


def reaching_tests(facts: Facts, key: str) -> list[str]:
    """Tests that call ``key`` directly or through exactly one non-test intermediate."""
    found: set[str] = set()
    for caller in facts.callers.get(key, ()):
        if facts.is_test(caller):
            found.add(caller)
        else:
            found.update(c for c in facts.callers.get(caller, ()) if facts.is_test(c))
    return sorted(found)


def _target_name(facts: Facts, names: dict[str, str], key: str) -> str:
    return names.get(key) or facts.definitions[key].qualname


def gen_multihop(
    facts: Facts, seed: int, ownership: Ownership | None = None, count: int = 4
) -> list[Question]:
    """Half "which tests reach X through at most one intermediate", half "which modules import
    code from files mostly changed by <author>" (when history is given and has candidates)."""
    names = display_names(facts)
    reach_pool = [
        d
        for d in facts.definitions.values()
        if _askable(d)
        and d.kind in ("function", "method")
        and d.key in names
        and REACH_MIN <= len(reaching_tests(facts, d.key)) <= REACH_MAX
        # at least one answer must need the intermediate hop
        and any(not facts.is_test(c) for c in facts.callers.get(d.key, ()))
    ]
    reach = stratified_order(
        reach_pool,
        lambda d: len(reaching_tests(facts, d.key)),
        lambda d: d.key,
        rng(seed, facts.repo_id, "multihop", "reach"),
    )
    owned = _ownership_candidates(facts, ownership) if ownership else []
    rng(seed, facts.repo_id, "multihop", "owners").shuffle(owned)
    owner_count = min(len(owned), count // 2)
    out: list[tuple[str, AnswerSetTruth, Provenance, str]] = []
    for d in reach[: count - owner_count]:
        text = (
            f"Which test functions reach `{names[d.key]}` (defined in `{d.path}`) directly "
            "or through exactly one intermediate function?"
        )
        answers = [facts.location(k) for k in reaching_tests(facts, d.key)]
        notes = "template: tests-reach-1. Intermediates are non-test functions, methods, classes."
        out.append((text, AnswerSetTruth(answers=answers), "scip", notes))
    for author, files, modules in owned[:owner_count]:
        text = (
            f"Which non-test modules import or use code from the files mostly changed by "
            f"{author}? (Mostly changed: {author} made more than half of the non-merge commits "
            "that touched the file, up to this commit.)"
        )
        answers = [Location(symbol_key=f"file:{p}", path=p, line=1) for p in modules]
        notes = "template: owner-modules. Owned files: " + ", ".join(files) + "."
        out.append((text, AnswerSetTruth(answers=answers), "git", notes))
    return [
        question(facts, "multihop", n, text, truth, provenance, notes)
        for n, (text, truth, provenance, notes) in enumerate(out, start=1)
    ]


def _ownership_candidates(
    facts: Facts, ownership: Ownership
) -> list[tuple[str, list[str], list[str]]]:
    """(author, owned files, modules that reference them) with 2 to 15 modules."""
    library = {
        d.path for d in facts.definitions.values() if not d.is_test_file and _is_library(d.path)
    }
    owned_by: dict[str, list[str]] = {}
    for path, author in sorted(ownership.owners.items()):
        if path in library:
            owned_by.setdefault(author, []).append(path)
    found = []
    for author in sorted(owned_by):
        files = owned_by[author]
        owned_keys = {k for k, d in facts.definitions.items() if d.path in files}
        modules = sorted(
            path
            for path, keys in facts.file_references.items()
            if path in library and path not in files and keys & owned_keys
        )
        if MODULES_MIN <= len(modules) <= MODULES_MAX:
            found.append((author, files, modules))
    return found


# --------------------------------------------------------------------------------------------
# Static impact (BEN-6)
# --------------------------------------------------------------------------------------------

IMPACT_DEPTH = 3


def transitive_callers(facts: Facts, key: str, depth: int = IMPACT_DEPTH) -> list[str]:
    """Every definition that reaches ``key`` through at most ``depth`` caller hops."""
    seen: set[str] = set()
    frontier = {key}
    for _ in range(depth):
        nxt: set[str] = set()
        for k in sorted(frontier):
            nxt.update(facts.callers.get(k, ()))
        nxt -= seen | {key}
        seen |= nxt
        frontier = nxt
    return sorted(seen)


def gen_static_impact(
    facts: Facts, seed: int, count: int = 4, targets: Sequence[str] | None = None
) -> list[Question]:
    """ "What breaks if I change `X`?": transitive callers up to depth 3, tests included.
    ``targets`` restricts the sample (the sandbox's mutated functions, so real failures can be
    joined in); otherwise askable functions and methods with at least one caller are used."""
    names = display_names(facts)
    if targets is not None:
        pool = [facts.definitions[k] for k in sorted(set(targets)) if k in facts.definitions]
    else:
        pool = [
            d
            for d in facts.definitions.values()
            if _askable(d) and d.kind in ("function", "method") and facts.callers.get(d.key)
        ]
    order = stratified_order(
        pool,
        lambda d: len(transitive_callers(facts, d.key)),
        lambda d: d.key,
        rng(seed, facts.repo_id, "impact"),
    )
    out = []
    for n, d in enumerate(order[:count], start=1):
        name = _target_name(facts, names, d.key)
        text = (
            f"What breaks if I change `{name}` (defined in `{d.path}`)? List the functions, "
            "methods, and tests that depend on it."
        )
        static = [facts.location(k) for k in transitive_callers(facts, d.key)]
        truth = ImpactTruth(target=facts.location(d.key), static=static, real_failing_tests=None)
        notes = f"static: callers up to depth {IMPACT_DEPTH} ({len(static)})."
        out.append(question(facts, "impact", n, text, truth, "scip", notes))
    return out


# --------------------------------------------------------------------------------------------
# Real impact from the sandbox (BEN-7)
# --------------------------------------------------------------------------------------------


class RealImpactStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    joined: int  # impact questions given real failures
    failing_tests: int  # confirmed failing tests across them
    unmapped_tests: int  # of which the test ID did not map to a symbol key


def confirmed_targets(records: Sequence[MutationRecord]) -> list[str]:
    """Mutated functions with a confirmed, non-empty failure set."""
    return sorted(r.symbol_key for r in records if r.has_failures)


def import_real_impact(
    questions: Sequence[Question], records: Sequence[MutationRecord]
) -> tuple[list[Question], RealImpactStats]:
    """Fill ``real_failing_tests`` of impact questions whose target was mutated with confirmed
    failures. Unmapped test IDs are kept with ``symbol_key = None`` and counted."""
    by_key = {r.symbol_key: r for r in records if r.has_failures}
    out: list[Question] = []
    joined = failing = unmapped = 0
    for q in questions:
        truth = q.ground_truth
        record = by_key.get(truth.target.symbol_key) if isinstance(truth, ImpactTruth) else None
        if record is None or not isinstance(truth, ImpactTruth):
            out.append(q)
            continue
        tests = sorted(
            (RealFailingTest(test_id=t.test_id, symbol_key=t.symbol_key) for t in record.failing),
            key=lambda t: t.test_id,
        )
        missing = sum(t.symbol_key is None for t in tests)
        joined += 1
        failing += len(tests)
        unmapped += missing
        notes = (
            f"{q.notes} real: {len(tests)} confirmed failing tests from the sandbox mutation "
            f"({record.kind}, seed {record.seed}); {missing} unmapped."
        ).strip()
        new_truth = truth.model_copy(update={"real_failing_tests": tests})
        out.append(
            q.model_copy(
                update={"ground_truth": new_truth, "provenance": "sandbox", "notes": notes}
            )
        )
    return out, RealImpactStats(joined=joined, failing_tests=failing, unmapped_tests=unmapped)
