"""Scorers (EVL-2): per-question metrics and the aggregates reports show.

Per question (pure functions of the ground truth and a ``SystemOutput``):

- ``lookup_correct`` — a valid citation's range contains the definition line in the definition's
  path. An answer with no citations at all (closed book) is correct when its predicted symbols
  include the definition and the answer names the definition's path.
- ``set_prf`` — precision, recall, and F1 of a predicted set of symbol keys against the truth.
  An empty prediction has precision 0 (it found nothing); an empty truth has no recall, and F1
  is 1 only when the prediction is empty too.
- ``recall_at_k`` — the share of the truth found in the first ``k`` ranked predictions.
- ``must_cite_recall`` — cited symbols ∩ must-cite ÷ must-cite (flow questions).
- ``refusal_outcome`` — unanswerable questions are correct only when refused for evidence
  reasons; API refusals are counted on their own and are never correct.

``score_benchmark`` dispatches on the question type and returns an ``ItemScore`` whose
``primary`` is the type's main metric: lookup correct, callers/multihop F1, impact static F1
(real-failure precision, recall, and recall@10 as extra metrics), flow must-cite recall, and
unanswerable correct refusal. An answerable question that was refused scores 0; an error scores
``None`` (listed in reports, left out of means).

Aggregates over many outputs: ``mean``, ``percentile``, ``aggregate_outputs`` (citation validity
before filtering, dropped-claim rate, claim support, cost, latency p50/p95, refusal counts).
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from repoatlas.bench.schema import (
    AnswerSetTruth,
    FlowTruth,
    ImpactTruth,
    Location,
    LookupTruth,
    UnanswerableTruth,
)
from repoatlas.evals.outputs import ItemScore, SystemOutput
from repoatlas.evals.registry import EvalItem
from repoatlas.parsing.symbols import parse_symbol_key
from repoatlas.retrieval.evidence import EvidenceRef

REAL_K = 10  # recall@k for real failing tests


@dataclass(frozen=True)
class PRF:
    precision: float | None
    recall: float | None
    f1: float


def set_prf(predicted: Iterable[str], truth: Iterable[str]) -> PRF:
    pred, true = set(predicted), set(truth)
    hits = len(pred & true)
    if not true:
        return PRF(precision=0.0 if pred else None, recall=None, f1=0.0 if pred else 1.0)
    precision = hits / len(pred) if pred else 0.0
    recall = hits / len(true)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PRF(precision=precision, recall=recall, f1=f1)


def recall_at_k(ranked: Sequence[str], truth: Iterable[str], k: int) -> float | None:
    """|first k distinct predictions ∩ truth| ÷ |truth| (``None`` for an empty truth)."""
    if k < 1:
        raise ValueError("k must be positive.")
    true = set(truth)
    if not true:
        return None
    top = list(dict.fromkeys(ranked))[:k]
    return len(set(top) & true) / len(true)


def must_cite_recall(cited: Iterable[str], must_cite: Iterable[str]) -> float:
    must = set(must_cite)
    if not must:
        raise ValueError("A flow question needs must-cite symbols.")
    return len(set(cited) & must) / len(must)


def lookup_correct(definition: Location, output: SystemOutput) -> bool:
    if output.refused or output.error is not None:
        return False
    if output.citations:
        return any(_contains(ref, definition) for ref in output.citations)
    named = definition.path in output.answer_markdown or any(
        definition.path in claim.text for claim in output.claims
    )
    return definition.symbol_key in output.answer_keys and named


def _contains(ref: EvidenceRef, location: Location) -> bool:
    return ref.path == location.path and ref.start_line <= location.line <= ref.end_line


RefusalResult = str  # correct_refusal | api_refusal | budget_refusal | answered | error


def refusal_outcome(output: SystemOutput) -> RefusalResult:
    """How an unanswerable question went: only an evidence refusal is correct."""
    return {
        "refused_evidence": "correct_refusal",
        "refused_api": "api_refusal",
        "refused_budget": "budget_refusal",
        "answered": "answered",
        "error": "error",
    }[output.outcome]


def is_test_key(key: str) -> bool:
    try:
        return parse_symbol_key(key).kind == "test"
    except ValueError:
        return False


_TARGET = re.compile(r"`(?P<name>[^`]+)`\s*\(defined in `(?P<path>[^`]+)`\)")


def question_target(text: str) -> tuple[str, str] | None:
    """The symbol a callers, multihop, or impact question is about: (name, path)."""
    match = _TARGET.search(text)
    return (match["name"], match["path"]) if match else None


def _is_target(key: str, target: tuple[str, str] | None) -> bool:
    if target is None:
        return False
    name, path = target
    try:
        parts = parse_symbol_key(key)
    except ValueError:
        return False
    return parts.path == path and (parts.qualname == name or parts.qualname.endswith("." + name))


def predicted_keys(item: EvalItem, output: SystemOutput) -> list[str]:
    """The answer set, without the question's own target (it is never an answer)."""
    if output.impact is not None:
        keys = [entry.symbol_key for entry in output.impact]
    else:
        keys = list(output.answer_keys)
    target = question_target(item.text)
    truth = item.truth
    target_key = truth.target.symbol_key if isinstance(truth, ImpactTruth) else None
    return [key for key in dict.fromkeys(keys) if key != target_key and not _is_target(key, target)]


def _zero_if_refused(output: SystemOutput, value: float | None) -> float | None:
    if output.error is not None or output.outcome == "error":
        return None
    if output.refused:
        return 0.0
    return value


def score_benchmark(item: EvalItem, output: SystemOutput) -> ItemScore:
    """Score one benchmark question (M09 ground truth)."""
    truth = item.truth
    metrics: dict[str, float | None] = {}
    primary: float | None
    if isinstance(truth, LookupTruth):
        primary = float(lookup_correct(truth.definition, output))
    elif isinstance(truth, AnswerSetTruth):
        prf = set_prf(predicted_keys(item, output), [a.symbol_key for a in truth.answers])
        metrics.update(precision=prf.precision, recall=prf.recall)
        primary = prf.f1
    elif isinstance(truth, ImpactTruth):
        predicted = predicted_keys(item, output)
        prf = set_prf(predicted, [s.symbol_key for s in truth.static])
        metrics.update(static_precision=prf.precision, static_recall=prf.recall)
        if truth.real_failing_tests is not None:
            failing = {t.symbol_key for t in truth.real_failing_tests if t.symbol_key}
            tests = [key for key in predicted if is_test_key(key)]
            if output.impact is not None:
                tests = [e.symbol_key for e in output.impact if e.group == "test"] or tests
            real = set_prf(tests, failing)
            metrics.update(
                real_precision=real.precision,
                real_recall=real.recall,
                real_f1=real.f1,
                real_recall_at_10=recall_at_k(tests, failing, REAL_K),
            )
        primary = prf.f1
    elif isinstance(truth, FlowTruth):
        cited = [*output.cited_keys, *output.answer_keys]
        primary = must_cite_recall(cited, [m.symbol_key for m in truth.must_cite])
        if output.judge is not None:
            metrics["judge"] = output.judge.total
    elif isinstance(truth, UnanswerableTruth):
        result = refusal_outcome(output)
        metrics["api_refusal"] = float(result == "api_refusal")
        primary = None if result == "error" else float(result == "correct_refusal")
        return ItemScore(
            question_id=item.id,
            type=item.type,
            outcome=output.outcome,
            primary=primary,
            metrics=metrics,
        )
    else:
        raise TypeError(f"No scorer for ground truth {type(truth).__name__}.")
    metrics = {key: _zero_if_refused(output, value) for key, value in metrics.items()}
    return ItemScore(
        question_id=item.id,
        type=item.type,
        outcome=output.outcome,
        primary=_zero_if_refused(output, primary),
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def percentile(values: Iterable[float], q: float) -> float | None:
    """The ``q``-th percentile (0-100) with linear interpolation between ranks."""
    data = sorted(values)
    if not data:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100.")
    position = (len(data) - 1) * q / 100
    low, high = math.floor(position), math.ceil(position)
    return data[low] + (data[high] - data[low]) * (position - low)


class Aggregates(BaseModel):
    model_config = ConfigDict(frozen=True)

    outputs: int
    outcomes: dict[str, int]
    prefilter_citation_validity: float | None  # mean over answers that cited anything
    pooled_citation_validity: float | None  # valid ÷ all citations
    dropped_claim_rate: float | None
    claims_checked: int
    claim_supported: float | None  # share of checked claims labeled supported
    claim_partial: float | None
    claim_unsupported: float | None
    cost_per_question_usd: float | None  # system spend
    eval_cost_usd: float  # checker and judge spend, all outputs
    total_cost_usd: float
    latency_p50_s: float | None
    latency_p95_s: float | None


def aggregate_outputs(outputs: Sequence[SystemOutput]) -> Aggregates:
    ran = [output for output in outputs if output.outcome != "error"]
    labels = Counter(label.label for output in outputs for label in (output.support or []))
    checked = sum(labels.values())
    total_citations = sum(output.citations_total for output in ran)
    return Aggregates(
        outputs=len(outputs),
        outcomes=dict(sorted(Counter(output.outcome for output in outputs).items())),
        prefilter_citation_validity=mean(o.prefilter_citation_validity for o in ran),
        pooled_citation_validity=(
            sum(o.citations_valid for o in ran) / total_citations if total_citations else None
        ),
        dropped_claim_rate=mean(o.dropped_claim_rate for o in ran),
        claims_checked=checked,
        claim_supported=labels["supported"] / checked if checked else None,
        claim_partial=labels["partial"] / checked if checked else None,
        claim_unsupported=labels["unsupported"] / checked if checked else None,
        cost_per_question_usd=mean(o.cost_usd for o in outputs) if outputs else None,
        eval_cost_usd=sum(o.eval_cost_usd for o in outputs),
        total_cost_usd=sum(o.cost_usd + o.eval_cost_usd for o in outputs),
        latency_p50_s=percentile([o.latency_s for o in ran], 50),
        latency_p95_s=percentile([o.latency_s for o in ran], 95),
    )
