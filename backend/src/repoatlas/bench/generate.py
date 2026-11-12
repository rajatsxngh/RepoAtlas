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

from repoatlas.bench.facts import Definition, Facts
from repoatlas.bench.schema import (
    AnswerSetTruth,
    GroundTruth,
    LookupTruth,
    Provenance,
    Question,
    QuestionType,
)

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
