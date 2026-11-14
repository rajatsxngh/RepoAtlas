"""How often the detector fires on a stored snapshot (GRD-3): its false-positive measurement.

The benchmark repositories are ordinary open-source code with no planted instructions, so every
flag on them is treated as a false positive. The unit that matters is the chunk: one chunk is
one evidence block in a prompt, and a chunk with a flag at or above ``MARK_SEVERITY`` would be
shown to the model (and the user) with a warning it doesn't deserve.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel
from sqlalchemy import Engine, text

from repoatlas.guardrails.detector import DETECTOR_VERSION, MARK_SEVERITY, scan, scan_path

MAX_SAMPLES = 40


class FlagSample(BaseModel):
    path: str
    start_line: int
    kind: str
    severity: float
    snippet: str


class SnapshotScan(BaseModel):
    snapshot_id: int
    repo: str
    commit: str
    chunks: int
    chunks_flagged: int  # any flag at all
    chunks_marked: int  # a flag at or above MARK_SEVERITY
    marked_rate: float
    flags_by_kind: dict[str, int]
    marked_by_kind: dict[str, int]
    paths: int
    paths_marked: int
    samples: list[FlagSample]  # marked flags, most severe first


class DetectorMeasurement(BaseModel):
    detector_version: int = DETECTOR_VERSION
    mark_severity: float = MARK_SEVERITY
    snapshots: list[SnapshotScan]
    chunks: int
    chunks_marked: int
    marked_rate: float


def scan_snapshot(engine: Engine, snapshot_id: int) -> SnapshotScan:
    with engine.connect() as connection:
        head = connection.execute(
            text(
                "SELECT r.owner, r.name, s.commit_sha FROM snapshots s "
                "JOIN repos r ON r.id = s.repo_id WHERE s.id = :id"
            ),
            {"id": snapshot_id},
        ).first()
        if head is None:
            raise LookupError(f"Snapshot {snapshot_id} does not exist.")
        rows = connection.execute(
            text(
                "SELECT path, start_line, text FROM chunks WHERE snapshot_id = :id "
                "ORDER BY path, start_line"
            ),
            {"id": snapshot_id},
        ).all()
    by_kind: Counter[str] = Counter()
    marked_by_kind: Counter[str] = Counter()
    flagged = marked = 0
    samples: list[FlagSample] = []
    for row in rows:
        flags = scan(row.text)
        if not flags:
            continue
        flagged += 1
        severe = [flag for flag in flags if flag.severity >= MARK_SEVERITY]
        marked += bool(severe)
        for flag in flags:
            by_kind[flag.kind] += 1
        for flag in severe:
            marked_by_kind[flag.kind] += 1
            samples.append(
                FlagSample(
                    path=row.path,
                    start_line=row.start_line,
                    kind=flag.kind,
                    severity=flag.severity,
                    snippet=flag.snippet,
                )
            )
    paths = sorted({row.path for row in rows})
    paths_marked = sum(
        1 for path in paths if any(f.severity >= MARK_SEVERITY for f in scan_path(path))
    )
    samples.sort(key=lambda s: (-s.severity, s.path, s.start_line, s.kind))
    return SnapshotScan(
        snapshot_id=snapshot_id,
        repo=f"{head.owner}/{head.name}",
        commit=head.commit_sha,
        chunks=len(rows),
        chunks_flagged=flagged,
        chunks_marked=marked,
        marked_rate=round(marked / len(rows), 6) if rows else 0.0,
        flags_by_kind=dict(sorted(by_kind.items())),
        marked_by_kind=dict(sorted(marked_by_kind.items())),
        paths=len(paths),
        paths_marked=paths_marked,
        samples=samples[:MAX_SAMPLES],
    )


def measure(engine: Engine, snapshot_ids: list[int]) -> DetectorMeasurement:
    scans = [scan_snapshot(engine, snapshot_id) for snapshot_id in snapshot_ids]
    chunks = sum(s.chunks for s in scans)
    marked = sum(s.chunks_marked for s in scans)
    return DetectorMeasurement(
        snapshots=scans,
        chunks=chunks,
        chunks_marked=marked,
        marked_rate=round(marked / chunks, 6) if chunks else 0.0,
    )


def render(measurement: DetectorMeasurement) -> str:
    lines = [
        "# Injection detector on the benchmark repositories",
        "",
        f"Detector version {measurement.detector_version}. A chunk is *marked* when it has a "
        f"flag of severity {measurement.mark_severity} or more: that is what a prompt and the "
        "UI would show with a warning. These repositories have no planted instructions, so "
        "every marked chunk is a false positive.",
        "",
        f"**Marked chunks: {measurement.chunks_marked} of {measurement.chunks} "
        f"({measurement.marked_rate:.3%}).**",
        "",
        "| Repository | Commit | Chunks | With any flag | Marked | Marked rate | Marked paths |",
        "|---|---|---|---|---|---|---|",
    ]
    for scan_ in measurement.snapshots:
        lines.append(
            f"| {scan_.repo} | `{scan_.commit[:12]}` | {scan_.chunks} | {scan_.chunks_flagged} "
            f"| {scan_.chunks_marked} | {scan_.marked_rate:.3%} | {scan_.paths_marked} of "
            f"{scan_.paths} |"
        )
    lines += ["", "## Flags by kind (all severities / marked)", ""]
    lines += ["| Repository | Kind | Flags | Marked |", "|---|---|---|---|"]
    for scan_ in measurement.snapshots:
        for kind, count in scan_.flags_by_kind.items():
            lines.append(
                f"| {scan_.repo} | `{kind}` | {count} | {scan_.marked_by_kind.get(kind, 0)} |"
            )
    lines += ["", "## Marked flags (the false positives)", ""]
    any_sample = False
    for scan_ in measurement.snapshots:
        for sample in scan_.samples:
            any_sample = True
            snippet = sample.snippet.replace("|", "\\|").replace("`", "'")
            lines.append(
                f"- {scan_.repo} `{sample.path}:{sample.start_line}` `{sample.kind}` "
                f"{sample.severity:.2f}: `{snippet}`"
            )
    if not any_sample:
        lines.append("None.")
    return "\n".join(lines) + "\n"
