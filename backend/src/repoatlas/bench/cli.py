"""``repoatlas bench``: build, check, and freeze the benchmark (``eval/benchmark/``)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from repoatlas.bench.split import Splits

QUESTIONS_JSONL = "questions.jsonl"

bench_app = typer.Typer(
    help="The benchmark: questions with independent ground truth (eval/benchmark/).",
    no_args_is_help=True,
)

QuestionsOpt = Annotated[
    Path | None,
    typer.Option("--questions", help="Questions file (default: eval/benchmark/questions.jsonl)."),
]


def _questions_path(path: Path | None) -> Path:
    from repoatlas.bench.repos import benchmark_dir

    return path or benchmark_dir() / QUESTIONS_JSONL


@bench_app.command("validate")
def validate_command(questions_path: QuestionsOpt = None) -> None:
    """Check the schema, IDs, versions, and every location against the pinned commits."""
    from repoatlas.bench.repos import open_repo, recipes
    from repoatlas.bench.validate import parse_questions, validate

    path = _questions_path(questions_path)
    questions, problems = parse_questions(path.read_text(encoding="utf-8"))
    known = recipes()
    files = {}
    for repo_id in sorted({q.repo_id for q in questions} & set(known)):
        files[repo_id] = open_repo(known[repo_id])
    found = validate(questions, files, problems=problems)
    for problem in found:
        typer.echo(str(problem), err=True)
    if found:
        typer.echo(f"{len(found)} problems in {len(questions)} questions", err=True)
        raise typer.Exit(1)
    typer.echo(f"{len(questions)} questions valid ({len(files)} repos)")


@bench_app.command("keys")
def keys_command(
    repo: Annotated[str, typer.Option("--repo", help="Benchmark repo ID, e.g. click.")],
    paths: Annotated[list[str], typer.Argument(help="Repo-relative files to list.")],
) -> None:
    """List the symbol keys defined in files at the pinned commit (for writing flow questions)."""
    from repoatlas.bench.repos import open_repo, recipes
    from repoatlas.sandbox.parsed import parse_bytes

    known = recipes()
    if repo not in known:
        typer.echo(f"Unknown repo {repo}; known: {', '.join(sorted(known))}", err=True)
        raise typer.Exit(2)
    files = open_repo(known[repo])
    for path in paths:
        if not files.has(path):
            typer.echo(f"{path} does not exist at {files.commit[:12]}", err=True)
            raise typer.Exit(1)
        for symbol in parse_bytes(path, files.data(path)).symbols:
            typer.echo(f"{symbol.name_range.start_line:>6}  {symbol.key}")


SPLITS_JSON = "splits.json"
RepoOpt = Annotated[
    list[str],
    typer.Option("--repo", help="Benchmark repo ID (repeatable), or 'all'."),
]


def _read_splits(folder: Path) -> Splits | None:
    path = folder / SPLITS_JSON
    if not path.is_file():
        return None
    return Splits.model_validate_json(path.read_text(encoding="utf-8"))


@bench_app.command("generate")
def generate_command(
    repo: RepoOpt,
    seed: Annotated[int, typer.Option(help="Sampling seed (also seeds the split).")] = 1,
) -> None:
    """Regenerate the questions for the given repos and merge them into questions.jsonl."""
    from repoatlas.bench.build import build_repo
    from repoatlas.bench.repos import benchmark_dir, recipes
    from repoatlas.bench.split import assign_splits, check_frozen, make_splits, questions_text
    from repoatlas.bench.validate import read_questions
    from repoatlas.canonical import pretty_json, write_text_lf

    known = recipes()
    ids = sorted(known) if repo == ["all"] else sorted(set(repo))
    unknown = [r for r in ids if r not in known]
    if unknown:
        typer.echo(
            f"Unknown repo {', '.join(unknown)}; known: {', '.join(sorted(known))}", err=True
        )
        raise typer.Exit(2)
    folder = benchmark_dir()
    path = folder / QUESTIONS_JSONL
    existing = read_questions(path) if path.is_file() else []
    splits = _read_splits(folder)
    seeds = dict(splits.seeds) if splits else {}
    fresh = []
    for repo_id in ids:
        built = build_repo(known[repo_id], seed)
        seeds[repo_id] = seed
        fresh += built.questions
        stats = built.stats
        typer.echo(
            f"{repo_id}: {len(built.questions)} questions; {stats.definitions} SCIP definitions "
            f"with keys; {stats.history_commits} commits, {stats.owned_files} owned files; "
            f"real impact {stats.real_impact.joined} questions, "
            f"{stats.real_impact.failing_tests} failing tests "
            f"({stats.real_impact.unmapped_tests} unmapped); flow reviewed: {stats.flow_reviewed}"
        )
    merged = assign_splits([q for q in existing if q.repo_id not in ids] + fresh, seeds)
    if splits is not None:
        problem = check_frozen(splits, merged)
        if problem:
            typer.echo(
                f"The test split of {splits.benchmark_version} is frozen and {problem}. "
                "Bump BENCHMARK_VERSION to change test questions; nothing was written.",
                err=True,
            )
            raise typer.Exit(1)
    write_text_lf(path, questions_text(merged))
    new_splits = make_splits(merged, seeds, frozen=bool(splits and splits.frozen))
    if splits is not None and splits.benchmark_version != new_splits.benchmark_version:
        new_splits = new_splits.model_copy(update={"frozen": False})
    write_text_lf(folder / SPLITS_JSON, pretty_json(new_splits))
    typer.echo(f"Wrote {len(merged)} questions; digest {new_splits.questions_sha256}")


@bench_app.command("split")
def split_command(
    freeze: Annotated[
        bool, typer.Option("--freeze", help="Freeze the test split (needs reviewed flow files).")
    ] = False,
) -> None:
    """Recompute splits.json from questions.jsonl, and optionally freeze the test split."""
    from repoatlas.bench.build import read_flow
    from repoatlas.bench.repos import benchmark_dir
    from repoatlas.bench.split import assign_splits, check_frozen, make_splits
    from repoatlas.bench.validate import read_questions
    from repoatlas.canonical import pretty_json, write_text_lf

    folder = benchmark_dir()
    questions = read_questions(folder / QUESTIONS_JSONL)
    splits = _read_splits(folder)
    if splits is None:
        typer.echo("No splits.json; run: repoatlas bench generate", err=True)
        raise typer.Exit(1)
    assigned = assign_splits(questions, splits.seeds)
    if assigned != sorted(questions, key=lambda q: q.id):
        typer.echo(
            "questions.jsonl splits differ from the seeded assignment; regenerate.", err=True
        )
        raise typer.Exit(1)
    problem = check_frozen(splits, questions)
    if problem:
        typer.echo(f"The frozen test split no longer matches: {problem}.", err=True)
        raise typer.Exit(1)
    if freeze:
        pending = []
        for repo_id in sorted({q.repo_id for q in questions}):
            flow = read_flow(repo_id)
            if flow is None or not flow.reviewed:
                pending.append(repo_id)
        if pending:
            typer.echo(f"Flow questions not reviewed yet: {', '.join(pending)}", err=True)
            raise typer.Exit(1)
    result = make_splits(questions, splits.seeds, frozen=freeze or splits.frozen)
    write_text_lf(folder / SPLITS_JSON, pretty_json(result))
    state = "frozen" if result.frozen else "not frozen"
    typer.echo(f"dev {len(result.dev)}, test {len(result.test)} ({state})")
    typer.echo(f"test sha256 {result.test_sha256}")


@bench_app.command("digest")
def digest_command(questions_path: QuestionsOpt = None) -> None:
    """Print the sha256 of the canonical questions.jsonl and of its test split."""
    from repoatlas.bench.split import questions_digest, questions_text, sha256_text, split_digest
    from repoatlas.bench.validate import read_questions

    path = _questions_path(questions_path)
    questions = read_questions(path)
    text = path.read_text(encoding="utf-8")
    if text != questions_text(questions):
        typer.echo("questions.jsonl is not canonical (sorted canonical JSON lines)", err=True)
        raise typer.Exit(1)
    assert sha256_text(text) == questions_digest(questions)
    typer.echo(f"questions {questions_digest(questions)}")
    typer.echo(f"test      {split_digest(questions)}")
