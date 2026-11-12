"""Edge calibration (EVL-10, decision 0013): RepoAtlas edges against SCIP, per tier and provenance.

For each calibration repo (a sandbox recipe outside the benchmark, ``sandbox/calibration/``),
RepoAtlas's graph is built from the pinned commit and each edge is checked against what the
repo's SCIP index says (``repoatlas.bench.facts``, the same facts the benchmark uses):

- ``calls`` (by tier T1-T6 and by provenance) — confirmed when SCIP has a non-import reference
  to the callee inside the caller (the benchmark's "caller of" relation). An edge is judged only
  when both files are in the SCIP index and SCIP knows the callee's definition; other edges are
  counted as unjudged.
- ``tested_by`` (by provenance) — confirmed when the test references the symbol (for edges
  between files, when the test file references a definition in the source file).
- ``imports`` (by provenance) — confirmed when the importing file references a definition in the
  imported file; judged only when SCIP has definitions in that file.

Precision comes with a 95% Wilson interval. ``fit_confidences`` turns pooled precision into new
confidences: the Laplace-smoothed precision ``(confirmed + 1) / (judged + 2)``, rounded to two
places and capped at ``MAX_FITTED``, for groups with at least ``MIN_JUDGED`` judged edges
(others keep their value). T6's
confidence is a total split across a call's candidates, so its fit is the smoothed precision
times the mean candidate count, capped at 1. Benchmark repos can be measured the same way, for
reports, but are never used for fitting.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from repoatlas.bench.facts import Facts
from repoatlas.graph import tiers
from repoatlas.graph.model import CodeGraph, Edge
from repoatlas.parsing.symbols import parse_symbol_key

MIN_JUDGED = 20
MAX_FITTED = 0.99  # a resolved call or test link is never certain (structure keeps 1.0)


class Tally(BaseModel):
    edges: int = 0
    judged: int = 0
    confirmed: int = 0
    candidates: int = 0  # T6: sum of candidate counts over judged edges

    def add(self, judged: bool, confirmed: bool, candidates: int = 1) -> None:
        self.edges += 1
        if judged:
            self.judged += 1
            self.confirmed += int(confirmed)
            self.candidates += candidates

    def merge(self, other: Tally) -> Tally:
        return Tally(
            edges=self.edges + other.edges,
            judged=self.judged + other.judged,
            confirmed=self.confirmed + other.confirmed,
            candidates=self.candidates + other.candidates,
        )


class Row(BaseModel):
    kind: str  # calls, tested_by, imports
    by: str  # tier or provenance
    group: str
    edges: int
    judged: int
    confirmed: int
    precision: float | None
    low: float | None
    high: float | None
    current: float | None  # the confidence in tiers.py now


class RepoCalibration(BaseModel):
    repo_id: str
    commit: str
    misresolved_references: int
    tallies: dict[str, Tally]  # "<kind>|<by>|<group>" → tally
    scip_call_pairs: int  # SCIP caller relations between symbols both sides know
    found_call_pairs: int  # of those, how many RepoAtlas has as a calls edge

    @property
    def call_recall(self) -> float | None:
        return self.found_call_pairs / self.scip_call_pairs if self.scip_call_pairs else None


class Calibration(BaseModel):
    repos: list[RepoCalibration]
    rows: list[Row]
    fitted: dict[str, float] = Field(default_factory=dict)  # group → new confidence
    used_for_fitting: bool


def wilson(confirmed: int, judged: int, z: float = 1.96) -> tuple[float, float] | None:
    if judged == 0:
        return None
    p = confirmed / judged
    denom = 1 + z * z / judged
    centre = (p + z * z / (2 * judged)) / denom
    half = z * math.sqrt(p * (1 - p) / judged + z * z / (4 * judged * judged)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _known(facts: Facts, key: str) -> bool:
    return key in facts.definitions


def _caller_known(facts: Facts, key: str) -> bool:
    return key in facts.definitions or key in facts.test_symbols or key in facts.redefinitions


def _file_path(key: str) -> str | None:
    return key[len("file:") :] if key.startswith("file:") else None


def _defined_paths(facts: Facts) -> set[str]:
    return {d.path for d in facts.definitions.values()}


def _referenced_paths(facts: Facts, path: str) -> set[str]:
    out = set()
    for key in facts.file_references.get(path, set()):
        try:
            out.add(parse_symbol_key(key).path)
        except ValueError:
            continue
    return out


def _key_path(key: str) -> str | None:
    path = _file_path(key)
    if path is not None:
        return path
    try:
        return parse_symbol_key(key).path
    except ValueError:
        return None


def judge_edge(edge: Edge, facts: Facts, defined_paths: set[str]) -> tuple[bool, bool]:
    """(judged, confirmed) for one edge. An edge is judged only when both of its files are in
    the SCIP index (an indexer that skips test files can't confirm calls from tests)."""
    kind = str(edge.kind)
    src_path, dst_path = _key_path(edge.src), _key_path(edge.dst)
    if src_path not in defined_paths or dst_path not in defined_paths:
        return False, False
    if kind == "calls":
        if not (_known(facts, edge.dst) and _caller_known(facts, edge.src)):
            return False, False
        return True, edge.src in facts.callers.get(edge.dst, set())
    if kind == "tested_by":
        if _file_path(edge.src) is not None or _file_path(edge.dst) is not None:
            # file-level evidence (an import): the test file references the source file
            assert src_path is not None and dst_path is not None
            return True, src_path in _referenced_paths(facts, dst_path)
        if not (_known(facts, edge.src) and _caller_known(facts, edge.dst)):
            return False, False
        return True, edge.dst in facts.callers.get(edge.src, set())
    if kind == "imports":
        assert src_path is not None and dst_path is not None
        return True, dst_path in _referenced_paths(facts, src_path)
    return False, False


def calibrate_graph(repo_id: str, commit: str, graph: CodeGraph, facts: Facts) -> RepoCalibration:
    tallies: dict[str, Tally] = defaultdict(Tally)
    defined = _defined_paths(facts)
    call_edges: set[tuple[str, str]] = set()
    for edge in graph.edges:
        kind = str(edge.kind)
        if kind not in ("calls", "tested_by", "imports"):
            continue
        judged, confirmed = judge_edge(edge, facts, defined)
        provenance = str(edge.provenance)
        tallies[f"{kind}|provenance|{provenance}"].add(judged, confirmed)
        if kind == "calls":
            call_edges.add((edge.src, edge.dst))
            tier = str(edge.meta.get("tier", "?"))
            raw = edge.meta.get("candidates", 1)
            candidates = raw if isinstance(raw, int) and raw > 0 else 1
            tallies[f"calls|tier|{tier}"].add(judged, confirmed, candidates)
    nodes = set(graph.nodes)
    pairs = [
        (caller, callee)
        for callee, callers in facts.callers.items()
        if callee in nodes
        for caller in callers
        if caller in nodes and caller != callee
    ]
    return RepoCalibration(
        repo_id=repo_id,
        commit=commit,
        misresolved_references=sum(facts.misresolved.values()),
        tallies=dict(sorted(tallies.items())),
        scip_call_pairs=len(pairs),
        found_call_pairs=sum(pair in call_edges for pair in pairs),
    )


def current_confidence(kind: str, by: str, group: str) -> float | None:
    if kind == "calls" and by == "tier":
        try:
            return tiers.TIERS[tiers.Tier(group)].confidence
        except ValueError:
            return None
    if kind == "tested_by" and by == "provenance":
        return {
            "import_resolver": tiers.TESTED_BY_IMPORT_CONFIDENCE,
            "convention": tiers.TESTED_BY_CONVENTION_CONFIDENCE,
        }.get(group)
    if kind == "imports" and by == "provenance":
        return tiers.IMPORT_CONFIDENCE
    return None


def pool(repos: Iterable[RepoCalibration]) -> dict[str, Tally]:
    pooled: dict[str, Tally] = defaultdict(Tally)
    for repo in repos:
        for name, tally in repo.tallies.items():
            pooled[name] = pooled[name].merge(tally)
    return dict(sorted(pooled.items()))


def rows(repos: Sequence[RepoCalibration]) -> list[Row]:
    out = []
    for name, tally in pool(repos).items():
        kind, by, group = name.split("|")
        interval = wilson(tally.confirmed, tally.judged)
        out.append(
            Row(
                kind=kind,
                by=by,
                group=group,
                edges=tally.edges,
                judged=tally.judged,
                confirmed=tally.confirmed,
                precision=tally.confirmed / tally.judged if tally.judged else None,
                low=interval[0] if interval else None,
                high=interval[1] if interval else None,
                current=current_confidence(kind, by, group),
            )
        )
    return out


def fit_confidences(repos: Sequence[RepoCalibration]) -> dict[str, float]:
    """New confidences for tiers and tested_by provenances with enough judged edges."""
    fitted: dict[str, float] = {}
    for name, tally in pool(repos).items():
        kind, by, group = name.split("|")
        if tally.judged < MIN_JUDGED:
            continue
        smoothed = (tally.confirmed + 1) / (tally.judged + 2)
        if kind == "calls" and by == "tier" and group in {t.value for t in tiers.Tier}:
            if group == tiers.Tier.T6.value:
                smoothed = min(1.0, smoothed * tally.candidates / tally.judged)
            fitted[f"calls.{group}"] = min(round(max(smoothed, 0.01), 2), MAX_FITTED)
        elif (
            kind == "tested_by"
            and by == "provenance"
            and group in ("import_resolver", "convention")
        ):
            fitted[f"tested_by.{group}"] = min(round(max(smoothed, 0.01), 2), MAX_FITTED)
    return fitted


def render_calibration(calibration: Calibration, title: str) -> str:
    def f(value: float | None) -> str:
        return "-" if value is None else f"{value:.3f}"

    lines = [
        f"# {title}",
        "",
        "Repos: "
        + ", ".join(
            f"{r.repo_id} @ {r.commit[:12]} (call recall {f(r.call_recall)}, "
            f"{r.misresolved_references} mis-resolved SCIP references ignored)"
            for r in calibration.repos
        ),
        "",
        "Precision = confirmed / judged, with 95% Wilson intervals. Unjudged edges touch a file "
        "SCIP did not index or a symbol SCIP has no definition for.",
        "",
        "| Edges | By | Group | Edges | Judged | Confirmed | Precision [95% CI] | Current |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in calibration.rows:
        ci = "-" if row.low is None else f"{f(row.precision)} [{f(row.low)}, {f(row.high)}]"
        lines.append(
            f"| {row.kind} | {row.by} | {row.group} | {row.edges} | {row.judged} | "
            f"{row.confirmed} | {ci} | {f(row.current)} |"
        )
    if calibration.used_for_fitting:
        lines += ["", f"Fitted confidences (groups with at least {MIN_JUDGED} judged edges):", ""]
        lines += [f"- {name}: {value:.2f}" for name, value in sorted(calibration.fitted.items())]
    else:
        lines += ["", "Measured only: these repos are not used for fitting."]
    return "\n".join(lines) + "\n"


def calibrate_recipe(recipe: object) -> RepoCalibration:
    """Build the graph and the SCIP facts for one recipe's commit and compare them."""
    from repoatlas.bench.build import load_index
    from repoatlas.bench.facts import build_facts
    from repoatlas.bench.repos import open_repo
    from repoatlas.config import get_settings
    from repoatlas.pipeline.graph import graph_tree
    from repoatlas.sandbox.parsed import parse_tree
    from repoatlas.sandbox.recipe import Recipe

    assert isinstance(recipe, Recipe)
    files = open_repo(recipe)
    parsed = parse_tree(files.tree)
    facts = build_facts(load_index(recipe), parsed, files)
    settings = get_settings()
    graph = graph_tree(
        files.tree,
        max_source_files=settings.max_source_files,
        max_file_bytes=settings.max_file_bytes,
    ).graph
    return calibrate_graph(recipe.id, recipe.commit, graph, facts)
