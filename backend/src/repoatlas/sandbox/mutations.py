"""Mutation runs (SBX-9, SBX-10): real test failures for each mutated function.

For each target the function body is replaced (``mutate``), the full suite runs on a layer over
the repo image, and the failing tests are re-run on their own. A test counts only if it fails
both times and is not quarantined, pre-failing, or a suite-level (collection) error. Failing
test IDs are mapped to test symbol keys; unmapped IDs keep ``symbol_key = None``.

Records are appended to ``mutations.jsonl`` one target at a time, so an interrupted run resumes
where it stopped.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from repoatlas.canonical import canonical_json
from repoatlas.ingest.source import SourceTree
from repoatlas.parsing.models import ParsedFile
from repoatlas.parsing.symbols import parse_symbol_key
from repoatlas.sandbox.baseline import FAILING, Baseline
from repoatlas.sandbox.mutate import MUTATION_KIND, SkipTarget, mutate_file
from repoatlas.sandbox.results import TestIndex
from repoatlas.sandbox.runner import SandboxRunner, TestRun
from repoatlas.sandbox.targets import Candidate

Status = Literal["confirmed", "no_failures", "skipped", "suite_error"]


class FailingTest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    test_id: str
    symbol_key: str | None


class MutationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol_key: str
    path: str
    kind: str
    seed: int
    stratum: int
    fan_in: int
    hops: int
    status: Status
    failing: list[FailingTest] = []  # confirmed: failed in the full run and the re-run
    unconfirmed: list[str] = []  # failed in the full run, passed in the re-run
    excluded: dict[str, int] = {}  # failures ignored: quarantined, pre_failing, collection
    detail: str | None = None  # skip reason or suite error
    runtime_s: float = 0.0

    @property
    def has_failures(self) -> bool:
        return self.status == "confirmed" and bool(self.failing)


def read_records(path: Path) -> list[MutationRecord]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(MutationRecord.model_validate_json(line))
    return records


def append_record(path: Path, record: MutationRecord) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()


def _failures(run: TestRun) -> set[str]:
    return {test_id for test_id, outcome in run.outcomes().items() if outcome in FAILING}


def mutate_target(
    runner: SandboxRunner,
    candidate: Candidate,
    *,
    source: bytes,
    baseline: Baseline,
    tests: TestIndex,
    seed: int,
) -> MutationRecord:
    path = parse_symbol_key(candidate.key).path
    base = MutationRecord(
        symbol_key=candidate.key,
        path=path,
        kind=MUTATION_KIND,
        seed=seed,
        stratum=candidate.stratum,
        fan_in=candidate.fan_in,
        hops=candidate.hops,
        status="skipped",
    )
    started = time.monotonic()
    try:
        mutation = mutate_file(path, source, candidate.key)
    except SkipTarget as exc:
        return base.model_copy(update={"detail": str(exc)})

    quarantined = set(baseline.quarantined)
    pre_failing = set(baseline.pre_failing)

    def to_confirm(full: TestRun) -> list[str]:
        return sorted(_failures(full) - quarantined - pre_failing)

    full, confirm = runner.run_confirmed(mutation, failing=to_confirm)
    elapsed = round(time.monotonic() - started, 2)
    if full.report_error is not None:
        return base.model_copy(
            update={"status": "suite_error", "detail": full.report_error, "runtime_s": elapsed}
        )
    failed = _failures(full)
    excluded = {
        "quarantined": len(failed & quarantined),
        "pre_failing": len(failed & pre_failing),
        "collection": len(full.collection_errors),
    }
    candidates = to_confirm(full)
    if confirm is None or confirm.report_error is not None:
        confirmed: set[str] = set()
    else:
        confirmed = set(candidates) & _failures(confirm)
    failing = [FailingTest(test_id=t, symbol_key=tests.map(t)) for t in sorted(confirmed)]
    return base.model_copy(
        update={
            "status": "confirmed" if failing else "no_failures",
            "failing": failing,
            "unconfirmed": sorted(set(candidates) - confirmed),
            "excluded": excluded,
            "detail": confirm.report_error if confirm is not None else None,
            "runtime_s": elapsed,
        }
    )


def run_mutations(
    runner: SandboxRunner,
    targets: Iterable[Candidate],
    *,
    tree: SourceTree,
    parsed: Mapping[str, ParsedFile],
    baseline: Baseline,
    seed: int,
    out: Path,
    progress: Callable[[MutationRecord], None] | None = None,
) -> list[MutationRecord]:
    """Mutate each target not already in ``out``; returns every record in ``out``."""
    done = {record.symbol_key for record in read_records(out)}
    tests = TestIndex(parsed)
    entries = {entry.path: entry for entry in tree.entries()}
    for candidate in targets:
        if candidate.key in done:
            continue
        path = parse_symbol_key(candidate.key).path
        record = mutate_target(
            runner,
            candidate,
            source=tree.read(entries[path]),
            baseline=baseline,
            tests=tests,
            seed=seed,
        )
        append_record(out, record)
        done.add(candidate.key)
        if progress is not None:
            progress(record)
    return read_records(out)
