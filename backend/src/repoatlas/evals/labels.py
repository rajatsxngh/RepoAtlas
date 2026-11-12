"""Human labels for the checker and the judge, and their agreement (EVL-3, EVL-4).

``export_claims`` samples checked claims from a dev report's cached outputs and writes a label
file for a person: each claim with the code it cites and an empty ``label``. ``export_flow``
does the same for flow answers (question, reference outline, must-cite symbols, the answer
truncated like the judge sees it, and empty rubric scores). The machine's labels go into a
separate ``.machine.json`` file, so the person labels blind.

``agreement(labels file)`` compares the filled-in labels with the machine's: accuracy and
Cohen's kappa for claim support; per-criterion accuracy, Cohen's kappa (unweighted and
quadratic-weighted), and the mean absolute difference of the totals for flow scores. Below
``EXPLORATORY_KAPPA`` the results are marked exploratory.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.evals.outputs import JudgeScore, SupportLabel, SystemOutput

EXPLORATORY_KAPPA = 0.6  # "substantial" agreement or better is reported as measured
SUPPORT_LABELS = ("supported", "partial", "unsupported")
RUBRIC = ("steps_correct", "coverage", "grounded")


def cohen_kappa(
    a: Sequence[Any],
    b: Sequence[Any],
    *,
    weights: str | None = None,
    categories: Sequence[Any] | None = None,
) -> float | None:
    """Cohen's kappa of two raters over the same items (``weights="quadratic"`` for ordinal
    integer categories). ``None`` when there is nothing to compare; 1.0 when both raters used a
    single, identical category throughout."""
    if len(a) != len(b):
        raise ValueError("Both raters must label the same items.")
    if not a:
        return None
    cats = list(categories) if categories is not None else sorted(set(a) | set(b), key=str)
    index = {c: i for i, c in enumerate(cats)}
    k, n = len(cats), len(a)
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        observed[index[x]][index[y]] += 1
    rows = [sum(r) for r in observed]
    cols = [sum(observed[i][j] for i in range(k)) for j in range(k)]

    def weight(i: int, j: int) -> float:
        if weights == "quadratic":
            return ((i - j) ** 2) / ((k - 1) ** 2) if k > 1 else 0.0
        return 0.0 if i == j else 1.0

    disagree_obs = sum(weight(i, j) * observed[i][j] for i in range(k) for j in range(k)) / n
    disagree_exp = sum(weight(i, j) * rows[i] * cols[j] for i in range(k) for j in range(k)) / (
        n * n
    )
    if disagree_exp == 0:
        return 1.0 if disagree_obs == 0 else 0.0
    return 1 - disagree_obs / disagree_exp


def accuracy(a: Sequence[Any], b: Sequence[Any]) -> float | None:
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a) if a else None


# ---------------------------------------------------------------------------
# Label files
# ---------------------------------------------------------------------------


class LabelFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["claim_support", "flow"]
    instructions: str
    source: dict[str, Any]
    labeler: str | None = None  # who filled it in
    items: list[dict[str, Any]]


CLAIM_INSTRUCTIONS = (
    "For each item, read the claim and only the code shown, then set label to supported (the "
    "code shows everything the claim says), partial (it shows part of it, or the claim goes "
    "further), or unsupported (it doesn't show it, or contradicts it). Set labeler at the top "
    "to your name. Don't look at the .machine.json file until you're done."
)
FLOW_INSTRUCTIONS = (
    "For each answer, score three criteria from 0 to 2 against the reference outline and the "
    "must-cite symbols: steps_correct (2 = every step it states is right, 1 = minor errors, "
    "0 = a wrong step), coverage (2 = all outline steps, 1 = about half, 0 = few), grounded "
    "(2 = names the real functions and files throughout, 1 = some, 0 = vague or invented). "
    "Set labeler at the top to your name. Don't look at the .machine.json file until you're done."
)


def write_label_file(path: Path, labels: LabelFile, machine: dict[str, Any]) -> tuple[Path, Path]:
    text = yaml.safe_dump(
        labels.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
    write_text_lf(path, text)
    machine_path = path.with_suffix(".machine.json")
    write_text_lf(machine_path, pretty_json(machine))
    return path, machine_path


def read_label_file(path: Path) -> tuple[LabelFile, dict[str, Any]]:
    labels = LabelFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    machine = json.loads(path.with_suffix(".machine.json").read_text(encoding="utf-8"))
    return labels, machine


def sample(items: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """``n`` items, deterministic for ``seed``, spread over questions before repeating one."""
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    seen: Counter[str] = Counter()
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in shuffled:
        ranked.append((seen[item["question_id"]], item))
        seen[item["question_id"]] += 1
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0])][:n]


def cached_outputs(report: Any, cache: Any) -> list[tuple[str, int, SystemOutput]]:
    """(question ID, run, output) for every item run in ``report``, read from the eval cache,
    with the report's checker and judge results loaded."""
    out: list[tuple[str, int, SystemOutput]] = []
    for item in report.items:
        for run in item.runs:
            data = cache.get("answers", report.config_fingerprint, item.question_id, run.run)
            if data is None:
                continue
            output = SystemOutput.model_validate(data)
            support = report.evaluators.get("support")
            if support:
                key = f"{support}-{report.config_fingerprint}"
                labels = cache.get("support", key, item.question_id, run.run)
                if labels is not None:
                    output = output.model_copy(
                        update={"support": [SupportLabel.model_validate(d) for d in labels["data"]]}
                    )
            judge = report.evaluators.get("judge")
            if judge:
                key = f"{judge}-{report.config_fingerprint}"
                scored = cache.get("judge", key, item.question_id, run.run)
                if scored is not None:
                    output = output.model_copy(
                        update={"judge": JudgeScore.model_validate(scored["data"])}
                    )
            out.append((item.question_id, run.run, output))
    return out


def export_claims(
    report: Any, cache: Any, read: Any, *, n: int = 30, seed: int = 7
) -> tuple[LabelFile, dict[str, Any]]:
    """A blind claim-support label file. ``read(snapshot_id, ref)`` returns cited code."""
    snapshots = {item.question_id: item.snapshot_id for item in report.items}
    candidates: list[dict[str, Any]] = []
    for question_id, run, output in cached_outputs(report, cache):
        machine = {label.claim_id: label.label for label in output.support or []}
        for claim in output.claims:
            if claim.id in machine and claim.refs:
                candidates.append(
                    {
                        "question_id": question_id,
                        "run": run,
                        "claim": claim,
                        "machine": machine[claim.id],
                    }
                )
    chosen = sample(candidates, n, seed)
    items: list[dict[str, Any]] = []
    machine_labels: dict[str, Any] = {}
    for index, entry in enumerate(chosen, start=1):
        claim = entry["claim"]
        item_id = f"s{index:02d}"
        snapshot_id = snapshots[entry["question_id"]]
        items.append(
            {
                "id": item_id,
                "question_id": entry["question_id"],
                "run": entry["run"],
                "claim_id": claim.id,
                "claim": claim.text,
                "code": {ref.to_compact(): read(snapshot_id, ref) for ref in claim.refs},
                "label": None,
            }
        )
        machine_labels[item_id] = entry["machine"]
    labels = LabelFile(
        kind="claim_support",
        instructions=CLAIM_INSTRUCTIONS,
        source={
            "report_system": report.system,
            "split": report.split,
            "config_fingerprint": report.config_fingerprint,
            "checker": report.evaluators.get("support"),
            "seed": seed,
        },
        items=items,
    )
    return labels, machine_labels


def export_flow(
    reports: Sequence[Any], cache: Any, questions: dict[str, Any], *, n: int = 20, seed: int = 7
) -> tuple[LabelFile, dict[str, Any]]:
    """A blind flow label file over several systems' dev reports: answers shuffled, system names
    left out (they go into the machine file with the judge's scores)."""
    from repoatlas.evals.judge import judged_text

    candidates: list[dict[str, Any]] = []
    for report in reports:
        for question_id, run, output in cached_outputs(report, cache):
            item = questions.get(question_id)
            if item is None or item.type != "flow" or output.judge is None or run != 0:
                continue
            candidates.append(
                {
                    "question_id": question_id,
                    "item": item,
                    "output": output,
                    "system": report.system,
                }
            )
    chosen = sample(candidates, n, seed)
    items: list[dict[str, Any]] = []
    machine: dict[str, Any] = {}
    for index, entry in enumerate(chosen, start=1):
        item_id = f"f{index:02d}"
        truth = entry["item"].truth
        judge = entry["output"].judge
        items.append(
            {
                "id": item_id,
                "question_id": entry["question_id"],
                "question": entry["item"].text,
                "reference_outline": truth.reference_outline,
                "must_cite": [loc.symbol_key for loc in truth.must_cite],
                "answer": judged_text(entry["output"]),
                "steps_correct": None,
                "coverage": None,
                "grounded": None,
            }
        )
        machine[item_id] = {
            "system": entry["system"],
            "steps_correct": judge.steps_correct,
            "coverage": judge.coverage,
            "grounded": judge.grounded,
        }
    labels = LabelFile(
        kind="flow",
        instructions=FLOW_INSTRUCTIONS,
        source={
            "systems": sorted({r.system for r in reports}),
            "split": "dev",
            "judge": sorted({r.evaluators.get("judge") or "" for r in reports}),
            "seed": seed,
        },
        items=items,
    )
    return labels, machine


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


class Agreement(BaseModel):
    kind: Literal["claim_support", "flow"]
    labeler: str | None
    items: int  # labeled by the person
    accuracy: float | None
    kappa: float | None
    per_criterion: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    total_mean_abs_diff: float | None = None
    distribution: dict[str, dict[str, int]] = Field(default_factory=dict)
    exploratory: bool
    note: str


def agreement(labels: LabelFile, machine: dict[str, Any]) -> Agreement:
    if labels.kind == "claim_support":
        pairs = [
            (item["label"], machine[item["id"]])
            for item in labels.items
            if item.get("label") in SUPPORT_LABELS and item["id"] in machine
        ]
        human = [h for h, _ in pairs]
        auto = [m for _, m in pairs]
        kappa = cohen_kappa(human, auto, categories=SUPPORT_LABELS)
        exploratory = kappa is None or kappa < EXPLORATORY_KAPPA
        return Agreement(
            kind="claim_support",
            labeler=labels.labeler,
            items=len(pairs),
            accuracy=accuracy(human, auto),
            kappa=kappa,
            distribution={
                "human": dict(Counter(human)),
                "machine": dict(Counter(auto)),
            },
            exploratory=exploratory,
            note=_note(kappa, len(pairs)),
        )
    rows = [
        (item, machine[item["id"]])
        for item in labels.items
        if item["id"] in machine and all(isinstance(item.get(c), int) for c in RUBRIC)
    ]
    per: dict[str, dict[str, float | None]] = {}
    for criterion in RUBRIC:
        human = [int(item[criterion]) for item, _ in rows]
        auto = [int(m[criterion]) for _, m in rows]
        per[criterion] = {
            "accuracy": accuracy(human, auto),
            "kappa": cohen_kappa(human, auto, categories=(0, 1, 2)),
            "weighted_kappa": cohen_kappa(human, auto, weights="quadratic", categories=(0, 1, 2)),
        }
    totals = [
        abs(sum(int(item[c]) for c in RUBRIC) - sum(int(m[c]) for c in RUBRIC)) for item, m in rows
    ]
    human_totals = [sum(int(item[c]) for c in RUBRIC) for item, _ in rows]
    auto_totals = [sum(int(m[c]) for c in RUBRIC) for _, m in rows]
    kappa = cohen_kappa(human_totals, auto_totals, weights="quadratic", categories=range(7))
    return Agreement(
        kind="flow",
        labeler=labels.labeler,
        items=len(rows),
        accuracy=accuracy(human_totals, auto_totals),
        kappa=kappa,
        per_criterion=per,
        total_mean_abs_diff=sum(totals) / len(totals) if totals else None,
        exploratory=kappa is None or kappa < EXPLORATORY_KAPPA,
        note=_note(kappa, len(rows)),
    )


def _note(kappa: float | None, n: int) -> str:
    if kappa is None:
        return "No human labels yet: results are exploratory."
    level = "measured" if kappa >= EXPLORATORY_KAPPA else "exploratory"
    return f"kappa {kappa:.2f} on {n} items: results are reported as {level}."


def render_agreement(result: Agreement) -> str:
    def f(value: float | None) -> str:
        return "-" if value is None else f"{value:.3f}"

    lines = [
        f"# Agreement: {result.kind} ({result.items} items, labeler {result.labeler or '?'})",
        "",
        f"- Accuracy: {f(result.accuracy)}",
        f"- Cohen's kappa{' (quadratic, totals)' if result.kind == 'flow' else ''}: "
        f"{f(result.kappa)}",
    ]
    if result.total_mean_abs_diff is not None:
        lines.append(f"- Mean absolute difference of totals (0-6): {f(result.total_mean_abs_diff)}")
    for criterion, values in result.per_criterion.items():
        lines.append(
            f"- {criterion}: accuracy {f(values['accuracy'])}, kappa {f(values['kappa'])}, "
            f"weighted kappa {f(values['weighted_kappa'])}"
        )
    for who, counts in result.distribution.items():
        lines.append(f"- {who} labels: {', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}")
    lines += ["", result.note]
    return "\n".join(lines) + "\n"
