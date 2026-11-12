"""``repoatlas bench``: build, check, and freeze the benchmark (``eval/benchmark/``)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
