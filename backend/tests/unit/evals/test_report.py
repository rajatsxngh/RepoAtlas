from __future__ import annotations

import json
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]
import pytest

from repoatlas.canonical import pretty_json
from repoatlas.evals.report import (
    ReportMeta,
    SnapshotUsed,
    build_report,
    read_report,
    render_markdown,
    report_schema,
    validate_report,
    write_report,
)
from repoatlas.evals.runner import run_eval
from tests.unit.evals.helpers import F, ScriptedSystem, answer, items, ref
from tests.unit.evals.test_runner import CALLERS, LOOKUP, UNANS, make_run

REPO_ROOT = Path(__file__).resolve().parents[4]

META = ReportMeta(
    suite_info={
        "benchmark_version": "1.0.0",
        "questions_sha256": "q" * 64,
        "test_sha256": "t" * 64,
    },
    models={"large": "claude-sonnet-5-5"},
    embedding_model="hash-256",
    snapshots=[
        SnapshotUsed(
            repo_id="r",
            snapshot_id=7,
            commit="c" * 40,
            config_fingerprint="f" * 64,
            embedding_model="hash-256",
        )
    ],
    notes=["test note"],
)


async def _result(tmp_path: Path, **script_overrides: object):
    script: dict[str, object] = {
        LOOKUP: answer([("c1", "f", [ref("src/m.py", 10, 20, F)])]),
        CALLERS: answer([("c1", "nobody", [ref("src/m.py", 10, 20)])], cost=0.02),
        UNANS: answer([], refused=True, kind="api", cost=0.0),
    }
    script.update(script_overrides)
    return await run_eval(make_run(tmp_path, ScriptedSystem(script), runs=2))


@pytest.mark.anyio
async def test_reports_hold_metrics_intervals_refusals_and_items(tmp_path: Path) -> None:
    result = await _result(tmp_path, **{LOOKUP: RuntimeError("boom")})
    report = build_report(result, META, created_at="2026-11-12T10:00:00+00:00")
    by_type = {t.type: t for t in report.by_type}
    assert [t.type for t in report.by_type] == ["lookup", "callers", "unanswerable"]
    assert by_type["callers"].primary is not None and by_type["callers"].primary.mean == 0.0
    assert by_type["lookup"].questions == 0 and by_type["lookup"].primary is None
    assert by_type["unanswerable"].metrics["api_refusal"] == 1.0
    assert report.refusals.unanswerable_refused_api == 2
    assert [(e.question_id, e.run) for e in report.errored] == [
        ("r-lookup-001", 0),
        ("r-lookup-001", 1),
    ]
    assert not report.complete
    assert report.cost_per_question_usd == pytest.approx(0.02 / 3, abs=1e-6)
    assert len(report.items) == 3 and len(report.items[0].runs) == 2
    assert report.environment.python
    validate_report(json.loads(report.model_dump_json()))


@pytest.mark.anyio
async def test_write_report_never_overwrites_and_round_trips(tmp_path: Path) -> None:
    report = build_report(await _result(tmp_path), META, created_at="2026-11-12T10:00:00+00:00")
    first = write_report(report, tmp_path / "reports")
    second = write_report(report, tmp_path / "reports")
    assert first.name == "2026-11-12-scripted-dev.json"
    assert second.name == "2026-11-12-scripted-dev-2.json"
    assert first.with_suffix(".md").is_file()
    assert first.with_suffix(".png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert read_report(first) == report
    markdown = render_markdown(report)
    assert "| **all** | primary | 3 |" in markdown
    assert "claude-sonnet-5-5" in markdown and "test note" in markdown


def test_invalid_reports_fail_validation() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate_report({"schema_version": "1.0"})


def test_the_committed_schema_is_current() -> None:
    committed = (REPO_ROOT / "eval" / "report.schema.json").read_text(encoding="utf-8")
    assert committed == pretty_json(report_schema()), "run `uv run repoatlas eval schema`"


def test_helpers_items_are_stable() -> None:
    assert [item.id for item in items()] == ["r-lookup-001", "r-callers-001", "r-unanswerable-001"]
