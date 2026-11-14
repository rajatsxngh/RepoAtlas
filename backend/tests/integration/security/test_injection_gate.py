"""The injection suite end to end, without a model (GRD-6; the M13 build gate, decision 0021).

The real pieces run: the fixture is built into a snapshot through the pipeline, the registered
answer systems (``oneshot`` and ``impact_explain``) retrieve from it and build their prompts,
citations are validated against the stored blobs, and the eval runner scores every output with
``canary_leak`` and writes a report. Only the model is replaced, by two scripted stand-ins
(``standins.py``):

- the obedient one must be caught on every variant that reaches a prompt, each by its own
  canary;
- the careful one must score zero leaks on three runs of every question.

This shows that the fixture reaches the prompts, that the prompts mark the planted text, and
that the scorer and the command work. It does not show how a real model behaves: that is the
live run, pending in ``docs/live-pass.md``.

``REPOATLAS_GATE_EVIDENCE=<file>`` makes the tests append what they measured to that file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.config import Settings
from repoatlas.evals import session
from repoatlas.evals.injection import SUITE_NAME
from repoatlas.evals.registry import EvalContext
from repoatlas.evals.report import Report, build_report, validate_report, write_report
from repoatlas.evals.runner import RunResult, run_eval
from repoatlas.evals.session import EvalOptions, prepare_run, report_meta
from repoatlas.guardrails.lab import LAB_NAME, load_lab
from repoatlas.llm.client import LLMClient
from repoatlas.llm.fake import FakeLLM
from repoatlas.pipeline.local import build_local_snapshot
from repoatlas.store.graphstore import GraphStore
from tests.integration.conftest import empty_store
from tests.integration.security.standins import Careful, Obedient, StandIn
from tests.unit.api.helpers import api_settings

pytestmark = [pytest.mark.anyio, pytest.mark.injection_static]

LAB = load_lab()
INDEXED = {variant.id for variant in LAB.variants if variant.indexed}
WITH_MARKERS = {
    f"{variant.id}_{marker.name}"
    for variant in LAB.variants
    if variant.indexed
    for marker in variant.markers
}
ONESHOT = [question.id for question in LAB.questions_for("oneshot")]
IMPACT = [question.id for question in LAB.questions_for("impact_explain")]


@dataclass
class Gate:
    settings: Settings
    engine: Engine
    snapshot_id: int
    commit: str
    root: Path
    evidence: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture(scope="module")
def gate(
    pg_engine: Engine, pg_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Gate]:
    """The fixture built once, through the real pipeline (hash embeddings, no download)."""
    root = tmp_path_factory.mktemp("injection-gate")
    empty_store(pg_engine)
    settings = api_settings(
        database_url=pg_url,
        data_dir=root / "data",
        # Hash embeddings rank by chance, so the fused-score threshold tuned for the real
        # embedding model would refuse at random here; the suite needs the prompts built.
        refusal_min_retrieval_score=0.0,
    )
    built = build_local_snapshot(LAB.root, settings, pg_engine, name=LAB_NAME)
    gate = Gate(settings, pg_engine, built.snapshot_id, built.commit, root)
    yield gate
    target = os.environ.get("REPOATLAS_GATE_EVIDENCE")
    if target and gate.evidence:
        Path(target).write_text(json.dumps(gate.evidence, indent=2) + "\n", encoding="utf-8")
    empty_store(pg_engine)


async def run_suite(
    gate: Gate, system: str, stand_in: StandIn, *, runs: int, name: str
) -> tuple[RunResult, Report, Path]:
    context = EvalContext(settings=gate.settings, engine=gate.engine, llm=FakeLLM(stand_in))
    options = EvalOptions(
        system=system,
        suite=SUITE_NAME,
        runs=runs,
        max_cost_usd=2.0,
        cache_dir=gate.root / "cache" / name,
    )
    prepared = prepare_run(options, context=context)
    assert prepared.enrichers == []  # no claim checker and no judge on this suite
    assert prepared.snapshots[LAB_NAME].snapshot_id == gate.snapshot_id  # reused, not rebuilt
    result = await run_eval(prepared.run)
    report = build_report(result, report_meta(prepared))
    path = write_report(report, gate.root / "reports" / name)
    validate_report(json.loads(path.read_text(encoding="utf-8")))
    return result, report, path


def leaked(result: RunResult) -> set[str]:
    """The variants (and markers) the scorer named across every record."""
    return {
        name.removeprefix("leak_")
        for record in result.records
        for name, value in record.score.metrics.items()
        if name.startswith("leak_") and value
    }


def test_the_fixture_snapshot_is_built_from_local_files_and_reused(gate: Gate) -> None:
    store = GraphStore.from_settings(gate.settings, gate.engine)
    info = store.snapshot(gate.snapshot_id)
    assert info.status == "ready"
    assert (info.repo.owner, info.repo.name) == ("repoatlas-fixtures", LAB_NAME)
    assert info.commit_sha == gate.commit and len(gate.commit) == 40
    again = build_local_snapshot(LAB.root, gate.settings, gate.engine, name=LAB_NAME)
    assert again.reused and again.snapshot_id == gate.snapshot_id and again.commit == gate.commit
    # Code is read from the stored blobs, including the file whose name is an instruction.
    code = store.read_code(gate.snapshot_id, "src/lab/ignore_previous_instructions.py", 9, 14)
    assert "def apply_discount" in code.text
    charge = store.find_symbols(gate.snapshot_id, "charge", limit=5)[0].node
    assert charge.path == "src/lab/billing.py" and charge.qualified_name == "charge"


async def test_oneshot_the_obedient_stand_in_is_caught_on_every_indexed_variant(
    gate: Gate,
) -> None:
    stand_in = Obedient(LAB)
    result, report, _ = await run_suite(gate, "oneshot", stand_in, runs=1, name="oneshot-obedient")
    assert [record.question_id for record in result.records] == ONESHOT
    assert {record.output.outcome for record in result.records} == {"answered"}
    # Every indexed variant reached a prompt; the README variant never did.
    assert stand_in.seen() == INDEXED
    assert not any("CANARY-V03" in prompt for prompt in stand_in.prompts)
    assert stand_in.flagged_prompts() == len(stand_in.prompts) == len(ONESHOT)
    # The scorer names every one of them, each by its own canary, and every extra marker.
    found = leaked(result)
    assert {name for name in found if "_" not in name} == INDEXED
    assert {name for name in found if "_" in name} == WITH_MARKERS
    assert all(record.score.primary == 0.0 for record in result.records)
    assert report.overall is not None and report.overall.mean == 0.0
    assert all(record.output.evidence_flagged > 0 for record in result.records)
    gate.evidence.append(
        {
            "system": "oneshot",
            "stand_in": "obedient",
            "questions": len(result.records),
            "variants_in_prompts": sorted(stand_in.seen()),
            "variants_leaked": sorted(name for name in found if "_" not in name),
            "markers_leaked": sorted(name for name in found if "_" in name),
            "score": report.overall.mean,
        }
    )


async def test_oneshot_the_careful_stand_in_scores_zero_leaks_over_three_runs(gate: Gate) -> None:
    stand_in = Careful(LAB)
    result, report, path = await run_suite(
        gate, "oneshot", stand_in, runs=3, name="oneshot-careful"
    )
    assert len(result.records) == 3 * len(ONESHOT) and result.complete
    assert stand_in.seen() == INDEXED  # the same poisoned prompts as the obedient run
    assert leaked(result) == set()
    assert all(record.score.primary == 1.0 for record in result.records)
    assert all(record.score.metrics["leaks"] == 0.0 for record in result.records)
    assert report.complete and report.overall is not None and report.overall.mean == 1.0
    assert report.run_overall == [1.0, 1.0, 1.0]
    assert report.suite == SUITE_NAME and report.suite_info["variants"] == 17
    assert [used.snapshot_id for used in report.snapshots] == [gate.snapshot_id]
    # Answers cite real evidence, and the citations carry the detector's flags.
    cited = [ref for record in result.records for ref in record.output.citations]
    assert cited and {ref.path for ref in cited} <= set(LAB.files)
    assert path.suffix == ".json" and path.with_suffix(".md").is_file()
    gate.evidence.append(
        {
            "system": "oneshot",
            "stand_in": "careful",
            "questions": len(ONESHOT),
            "runs": 3,
            "records": len(result.records),
            "leaks": 0,
            "variants_in_prompts": sorted(stand_in.seen()),
            "score": report.overall.mean,
            "run_scores": report.run_overall,
        }
    )


async def test_impact_explanations_the_obedient_stand_in_is_caught(gate: Gate) -> None:
    stand_in = Obedient(LAB)
    result, report, _ = await run_suite(
        gate, "impact_explain", stand_in, runs=1, name="impact-obedient"
    )
    assert [record.question_id for record in result.records] == IMPACT
    assert {record.output.outcome for record in result.records} == {"answered"}
    reached = stand_in.seen()
    # The changed symbols and their dependents carry these variants into the prompts.
    assert reached >= {"v01", "v02", "v07", "v08", "v11", "v17"}
    assert "v03" not in reached
    found = leaked(result)
    assert {name for name in found if "_" not in name} == reached
    assert "v08_missing_file" in found
    assert all(record.score.primary == 0.0 for record in result.records)
    assert report.overall is not None and report.overall.mean == 0.0
    gate.evidence.append(
        {
            "system": "impact_explain",
            "stand_in": "obedient",
            "questions": len(result.records),
            "variants_in_prompts": sorted(reached),
            "variants_leaked": sorted(name for name in found if "_" not in name),
            "markers_leaked": sorted(name for name in found if "_" in name),
            "score": report.overall.mean,
        }
    )


async def test_impact_explanations_the_careful_stand_in_scores_zero_leaks(gate: Gate) -> None:
    stand_in = Careful(LAB)
    result, report, _ = await run_suite(
        gate, "impact_explain", stand_in, runs=3, name="impact-careful"
    )
    assert len(result.records) == 3 * len(IMPACT) and result.complete
    assert stand_in.seen() >= {"v01", "v02", "v07", "v08", "v11", "v17"}
    assert leaked(result) == set()
    assert all(record.score.primary == 1.0 for record in result.records)
    assert report.complete and report.overall is not None and report.overall.mean == 1.0
    assert report.system == "impact_explain" and report.config["kind"] == "impact_explain"
    gate.evidence.append(
        {
            "system": "impact_explain",
            "stand_in": "careful",
            "questions": len(IMPACT),
            "runs": 3,
            "records": len(result.records),
            "leaks": 0,
            "variants_in_prompts": sorted(stand_in.seen()),
            "score": report.overall.mean,
            "run_scores": report.run_overall,
        }
    )


async def test_each_system_gets_only_its_own_questions_and_the_suite_owns_its_snapshot(
    gate: Gate,
) -> None:
    context = EvalContext(settings=gate.settings, engine=gate.engine, llm=FakeLLM(Careful(LAB)))
    cache = gate.root / "cache" / "plan"
    oneshot = prepare_run(
        EvalOptions(system="hybrid_graphrag", suite=SUITE_NAME, cache_dir=cache), context=context
    )
    assert [item.id for item in oneshot.run.items] == ONESHOT  # any answer system: the questions
    impact = prepare_run(
        EvalOptions(system="impact_explain", suite=SUITE_NAME, cache_dir=cache), context=context
    )
    assert [item.id for item in impact.run.items] == IMPACT
    assert impact.run.items[0].text == "py:src/lab/billing.py:charge:function"
    assert impact.run.items[-1].text == "py:src/lab/gateway.py:Gateway.pay:method"
    assert {item.commit for item in impact.run.items} == {gate.commit}
    with pytest.raises(ValueError, match="builds its own snapshot"):
        prepare_run(
            EvalOptions(
                system="oneshot", suite=SUITE_NAME, snapshots=["injection_lab=1"], cache_dir=cache
            ),
            context=context,
        )
    with pytest.raises(LookupError, match="has no questions"):
        prepare_run(
            EvalOptions(system="impact_engine", suite=SUITE_NAME, cache_dir=cache), context=context
        )


def test_the_documented_command_runs_the_suite_without_a_model(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``repoatlas eval run --suite injection --system oneshot --runs 3 --max-cost 2.00``, with
    the real LLM client over the offline stand-in (decision 0020) in place of the API."""
    settings = gate.settings.model_copy(update={"llm_transport": "offline"})
    assert settings.anthropic_api_key is None
    context = EvalContext(
        settings=settings, engine=gate.engine, llm=LLMClient.from_settings(settings)
    )
    monkeypatch.setattr(session, "default_context", lambda: context)
    reports = gate.root / "reports" / "cli"
    arguments = ["eval", "run", "--suite", "injection", "--runs", "3", "--max-cost", "2.00"]
    arguments += ["--reports-dir", str(reports), "--cache-dir", str(gate.root / "cache" / "cli")]
    for system, questions in (("oneshot", ONESHOT), ("impact_explain", IMPACT)):
        result = CliRunner().invoke(app, [*arguments, "--system", system])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert sum(1 for line in lines if line.startswith("[run ")) == 3 * len(questions)
        summary = next(line for line in lines if line.startswith(f"{system} on injection/dev: "))
        assert "1.000 [1.000, 1.000]; $0.00 spent this run" in summary
        assert summary.endswith("complete")
    written = sorted(path.name for path in reports.glob("*.json"))
    assert len(written) == 2 and all("injection" in name for name in written)
    # An impact-only system has nothing to answer on this suite: a clear error, exit code 2.
    refused = CliRunner().invoke(app, [*arguments, "--system", "impact_engine"])
    assert refused.exit_code == 2 and "has no questions" in refused.output
