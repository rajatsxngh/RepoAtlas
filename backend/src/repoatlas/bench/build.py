"""Generate every question for one repo from its artifacts (``repoatlas bench generate``).

Inputs, all fixed for a pinned commit:
- the source files (the sandbox's git cache) and their ``parse_file`` results, for keys;
- ``eval/artifacts/<repo>/<commit>/index.scip``, checked against ``scip.json``;
- ``mutations.jsonl`` when the sandbox mutated the repo;
- the commit history behind the pinned commit (blobless, cached), for ownership;
- ``eval/benchmark/flow/<repo>.md`` for the hand-written flow questions.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from repoatlas.bench import repos
from repoatlas.bench.facts import Facts, build_facts
from repoatlas.bench.flow import FlowFile, flow_questions, parse_flow_file
from repoatlas.bench.generate import (
    BENCHMARK_VERSION,
    RealImpactStats,
    confirmed_targets,
    gen_callers,
    gen_lookup,
    gen_multihop,
    gen_static_impact,
    gen_unanswerable,
    import_real_impact,
)
from repoatlas.bench.ownership import Ownership, history_ownership
from repoatlas.bench.repos import RepoFiles, open_repo
from repoatlas.bench.schema import Location, Question
from repoatlas.bench.scip import ScipIndex
from repoatlas.config import get_settings
from repoatlas.parsing.models import ParsedFile
from repoatlas.sandbox.artifacts import (
    MUTATIONS_JSONL,
    SCIP_INDEX,
    SCIP_JSON,
    read_model,
    recipe_dir,
    sha256_file,
)
from repoatlas.sandbox.mutations import MutationRecord, read_records
from repoatlas.sandbox.parsed import parse_tree
from repoatlas.sandbox.recipe import Recipe
from repoatlas.sandbox.runner import ScipArtifact

COUNTS = {"lookup": 6, "callers": 6, "multihop": 4, "impact": 4, "unanswerable": 2}
CORPUS_MAX_BYTES = 2_000_000


class RepoStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_id: str
    definitions: int
    misresolved_references: int  # ignored: the text does not name SCIP's target
    history_commits: int
    owned_files: int
    real_impact: RealImpactStats
    flow_reviewed: bool | None  # None when the repo has no flow file yet


class RepoBuild(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    questions: list[Question]
    stats: RepoStats


def load_index(recipe: Recipe) -> ScipIndex:
    scip = read_model(recipe, SCIP_JSON, ScipArtifact)
    path = recipe_dir(recipe) / SCIP_INDEX
    if scip is None or not path.is_file() or sha256_file(path) != scip.sha256:
        raise FileNotFoundError(
            f"{recipe.id}: no index.scip matching scip.json; run: repoatlas sandbox scip"
        )
    return ScipIndex.load(path)


def corpus(files: RepoFiles) -> str:
    """Every text file at the commit, for absence checks."""
    parts = []
    for path in files.paths():
        data = files.data(path)
        if len(data) <= CORPUS_MAX_BYTES and b"\0" not in data[:4096]:
            parts.append(data.decode("utf-8", "replace"))
    return "\n".join(parts)


Locate = Callable[[str], Location | None]


def locator(facts: Facts, parsed: dict[str, ParsedFile]) -> Locate:
    """Key → location: the SCIP definition, else the parsed symbol's name line."""
    symbols = {s.key: s for file in parsed.values() for s in file.symbols}

    def locate(key: str) -> Location | None:
        if key in facts.definitions or key in facts.test_symbols:
            return facts.location(key)
        symbol = symbols.get(key)
        if symbol is None:
            return None
        path = key.split(":")[1]
        return Location(symbol_key=key, path=path, line=symbol.name_range.start_line)

    return locate


def read_flow(repo_id: str, folder: Path | None = None) -> FlowFile | None:
    path = (folder or repos.benchmark_dir() / "flow") / f"{repo_id}.md"
    if not path.is_file():
        return None
    return parse_flow_file(repo_id, path.read_text(encoding="utf-8"))


def build_questions(
    facts: Facts,
    *,
    seed: int,
    text: str,
    ownership: Ownership | None,
    records: list[MutationRecord],
    flow: FlowFile | None,
    locate: Locate,
) -> tuple[list[Question], RealImpactStats]:
    targets = confirmed_targets(records) or None
    impact = gen_static_impact(facts, seed, COUNTS["impact"], targets=targets)
    impact, real = import_real_impact(impact, records)
    questions = [
        *gen_lookup(facts, seed, COUNTS["lookup"]),
        *gen_callers(facts, seed, COUNTS["callers"]),
        *gen_multihop(facts, seed, ownership, COUNTS["multihop"]),
        *impact,
        *gen_unanswerable(facts, text, seed, facts.repo_id, COUNTS["unanswerable"]),
    ]
    if flow is not None:
        questions += flow_questions(
            flow, commit=facts.commit, version=BENCHMARK_VERSION, locate=locate
        )
    return sorted(questions, key=lambda q: q.id), real


def build_repo(recipe: Recipe, seed: int) -> RepoBuild:
    files = open_repo(recipe)
    parsed = parse_tree(files.tree)
    facts = build_facts(load_index(recipe), parsed, files)
    cache = get_settings().data_dir / "bench" / "history" / f"{recipe.id}-{recipe.commit[:12]}"
    ownership = history_ownership(recipe.clone_url, recipe.commit, cache, files.paths())
    records = read_records(recipe_dir(recipe) / MUTATIONS_JSONL)
    flow = read_flow(recipe.id)
    questions, real = build_questions(
        facts,
        seed=seed,
        text=corpus(files),
        ownership=ownership,
        records=records,
        flow=flow,
        locate=locator(facts, parsed),
    )
    stats = RepoStats(
        repo_id=recipe.id,
        definitions=len(facts.definitions),
        misresolved_references=sum(facts.misresolved.values()),
        history_commits=ownership.commits,
        owned_files=len(ownership.owners),
        real_impact=real,
        flow_reviewed=None if flow is None else flow.reviewed,
    )
    return RepoBuild(questions=questions, stats=stats)
