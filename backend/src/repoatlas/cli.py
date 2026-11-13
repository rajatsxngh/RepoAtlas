"""Command line interface: ``repoatlas``."""

from __future__ import annotations

import signal
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType
from typing import Annotated, Any, NoReturn, cast

import typer
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from repoatlas import __version__
from repoatlas.bench.cli import bench_app
from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.config import get_settings
from repoatlas.db import migrate
from repoatlas.db.engine import create_db_engine, redact_url
from repoatlas.evals.cli import eval_app
from repoatlas.impact.cli import impact_app
from repoatlas.ingest.errors import IngestError
from repoatlas.jobs.worker import Worker
from repoatlas.logging import configure
from repoatlas.pipeline.build import BuildResult, run_build
from repoatlas.pipeline.graph import GraphOutput, run_graph
from repoatlas.pipeline.parse import ParseOutput, run_parse
from repoatlas.sandbox.cli import sandbox_app
from repoatlas.store.cache import Direction, Level
from repoatlas.store.errors import StoreError
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.results import SnapshotInfo

START_DB_HINT = "Start it with: docker compose -f infra/docker-compose.yml up -d"

app = typer.Typer(
    name="repoatlas",
    help="RepoAtlas: code maps, cited answers, and impact analysis.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
app.add_typer(db_app, name="db")
query_app = typer.Typer(
    help="Read a stored snapshot (debugging). Results are JSON on stdout.", no_args_is_help=True
)
app.add_typer(query_app, name="query")
api_app = typer.Typer(help="The HTTP API.", no_args_is_help=True)
app.add_typer(api_app, name="api")
models_app = typer.Typer(help="The embedding model.", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(bench_app, name="bench")
app.add_typer(eval_app, name="eval")
app.add_typer(impact_app, name="impact")


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"repoatlas {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_show_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Configure logging before any command runs."""
    configure(get_settings())


def _echo_json(text: str) -> None:
    """Write JSON to stdout as UTF-8 bytes. Text output would use the console code page, which
    on Windows (cp1252 when piped) cannot encode most of Unicode."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # a text-only stream
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _connection_failed(database_url: str, error: OperationalError) -> NoReturn:
    lines = str(error.orig if error.orig is not None else error).strip().splitlines()
    typer.secho(
        f"Could not connect to Postgres at {redact_url(database_url)}.",
        err=True,
        fg=typer.colors.RED,
    )
    if lines:
        typer.echo(f"Reason: {lines[0]}", err=True)
    typer.echo(f"Is it running? {START_DB_HINT}", err=True)
    raise typer.Exit(code=1)


@models_app.command("fetch")
def models_fetch() -> None:
    """Download the embedding model now, so nothing is downloaded on first use.

    The image builds run this. The model goes to REPOATLAS_MODEL_CACHE_DIR (default: models in
    the data directory); the hashing stand-in (hash-<dim>) needs no download.
    """
    from repoatlas.retrieval.embed import embedder_for

    settings = get_settings()
    embedder = embedder_for(settings)
    dim = len(embedder.embed_query("warm up"))
    where = settings.model_cache_dir or settings.data_dir / "models"
    typer.echo(f"{embedder.model_name}: {dim} dimensions, ready in {where}")


@db_app.command("upgrade")
def db_upgrade(
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply migrations up to REVISION (default: the latest).

    A database at a revision this version doesn't know was migrated by a newer release (this
    one is running again after a rollback). Migrations are backward compatible with the
    previous release, so there is nothing to do, and the command succeeds.
    """
    database_url = get_settings().database_url
    try:
        current = migrate.current_revision(database_url)
        if current is not None and current not in migrate.known_revisions():
            typer.echo(
                f"Database is at revision {current}, which this version doesn't know: a newer "
                "release migrated it. Nothing to do."
            )
            return
        migrate.upgrade(database_url, revision)
        current = migrate.current_revision(database_url)
    except OperationalError as error:
        _connection_failed(database_url, error)
    typer.echo(f"Database is at revision {current}.")


@db_app.command("current")
def db_current() -> None:
    """Show the database's current migration revision."""
    database_url = get_settings().database_url
    try:
        current = migrate.current_revision(database_url)
    except OperationalError as error:
        _connection_failed(database_url, error)
    typer.echo(current or "No migrations applied yet. Run: repoatlas db upgrade")


@db_app.command("revision")
def db_revision(
    message: Annotated[str, typer.Argument(help="Short description of the change.")],
    autogenerate: Annotated[
        bool, typer.Option(help="Compare models with the database to draft the migration.")
    ] = False,
) -> None:
    """Create a new migration file inside the package."""
    database_url = get_settings().database_url
    try:
        revision_id = migrate.create_revision(database_url, message, autogenerate=autogenerate)
    except OperationalError as error:
        _connection_failed(database_url, error)
    typer.echo(f"Created migration {revision_id}.")


@app.command("parse")
def parse_command(
    target: Annotated[str, typer.Argument(help="A public GitHub URL or a local directory.")],
    commit: Annotated[
        str | None, typer.Option(help="Full commit SHA (default: the default branch's head).")
    ] = None,
    json_out: Annotated[
        str | None,
        typer.Option("--json", help="Write the full result as JSON to this file ('-' = stdout)."),
    ] = None,
    keep_workdir: Annotated[
        bool, typer.Option(help="Keep the fetched git data for debugging.")
    ] = False,
) -> None:
    """Fetch a repository (or read a directory) and parse its source files."""
    started = time.perf_counter()
    try:
        output = run_parse(target, get_settings(), commit=commit, keep_workdir=keep_workdir)
    except IngestError as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    to_stdout = json_out == "-"
    if json_out is not None:
        text = pretty_json(output)
        if to_stdout:
            _echo_json(text)
        else:
            write_text_lf(Path(json_out), text)
    for line in _parse_summary(output, time.perf_counter() - started):
        typer.echo(line, err=to_stdout)


def _parse_summary(output: ParseOutput, elapsed: float) -> list[str]:
    inv, stats = output.inventory.stats, output.stats
    languages = ", ".join(f"{lang} {count:,}" for lang, count in inv.indexed_by_language.items())
    skipped = ", ".join(f"{reason} {count:,}" for reason, count in inv.skipped.items()) or "none"
    issue_rate = stats.files_with_issues / stats.files_parsed if stats.files_parsed else 0.0
    commit = f" @ {output.commit}" if output.commit else ""
    return [
        f"Source:   {output.source}{commit}",
        f"Files:    {stats.files_parsed:,} parsed ({languages or 'none'}); skipped: {skipped}",
        f"Found:    {stats.symbols:,} symbols, {stats.imports:,} imports, {stats.calls:,} calls, "
        f"{stats.tests:,} tests, {stats.routes:,} routes",
        f"Issues:   {stats.files_with_issues:,} files with syntax issues ({issue_rate:.1%})",
        f"Digest:   {output.digest}",
        f"Time:     {elapsed:.1f} s",
    ]


@app.command("graph")
def graph_command(
    target: Annotated[str, typer.Argument(help="A public GitHub URL or a local directory.")],
    commit: Annotated[
        str | None, typer.Option(help="Full commit SHA (default: the default branch's head).")
    ] = None,
    json_out: Annotated[
        str | None,
        typer.Option("--json", help="Write the graph as JSON to this file ('-' = stdout)."),
    ] = None,
    stats: Annotated[
        bool, typer.Option("--stats", help="Print counts by kind, tier, and outcome.")
    ] = False,
    history: Annotated[
        bool, typer.Option(help="Fetch commit history for file owners (GitHub only).")
    ] = True,
    keep_workdir: Annotated[
        bool, typer.Option(help="Keep the fetched git data for debugging.")
    ] = False,
) -> None:
    """Build the code graph of a repository (or a directory): nodes, edges, owners."""
    started = time.perf_counter()
    try:
        output = run_graph(
            target,
            get_settings(),
            commit=commit,
            keep_workdir=keep_workdir,
            with_history=history,
        )
    except IngestError as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    to_stdout = json_out == "-"
    if json_out is not None:
        text = pretty_json(output)
        if to_stdout:
            _echo_json(text)
        else:
            write_text_lf(Path(json_out), text)
    for line in _graph_summary(output, time.perf_counter() - started, detailed=stats):
        typer.echo(line, err=to_stdout)


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} {value:,}" for key, value in counts.items()) or "none"


def _graph_summary(output: GraphOutput, elapsed: float, *, detailed: bool) -> list[str]:
    stats = output.graph.stats
    commit = f" @ {output.commit}" if output.commit else ""
    nodes, edges = sum(stats.nodes_by_kind.values()), sum(stats.edges_by_kind.values())
    resolved = sum(stats.call_sites_by_tier.values())
    lines = [f"Source:   {output.source}{commit}"]
    if detailed:
        lines += [
            f"Nodes:    {nodes:,} ({_counts(stats.nodes_by_kind)})",
            f"Edges:    {edges:,} ({_counts(stats.edges_by_kind)})",
            f"Calls:    {stats.call_sites:,} sites; resolved {resolved:,} "
            f"({_counts(stats.call_sites_by_tier)})",
            f"          unresolved {stats.unresolved_calls:,} "
            f"({_counts(stats.unresolved_by_reason)}); external {stats.external_calls:,}; "
            f"builtin {stats.builtin_calls:,}",
            f"Imports:  {stats.import_statements:,} statements "
            f"({_counts(stats.imports_by_outcome)})",
            f"Owners:   {stats.files_with_owners:,} files from {stats.history_commits:,} commits",
        ]
    else:
        lines += [
            f"Graph:    {nodes:,} nodes, {edges:,} edges; {stats.unresolved_calls:,} unresolved "
            f"and {stats.external_calls:,} external calls",
        ]
    return [*lines, f"Digest:   {output.digest}", f"Time:     {elapsed:.1f} s"]


@app.command("build")
def build_command(
    url: Annotated[str, typer.Argument(help="A public GitHub URL: https://github.com/<o>/<r>.")],
    commit: Annotated[
        str | None, typer.Option(help="Full commit SHA (default: the default branch's head).")
    ] = None,
    keep_workdir: Annotated[
        bool, typer.Option(help="Keep the fetched git data for debugging.")
    ] = False,
) -> None:
    """Build a repository's snapshot into the store: fetch, inventory, parse, graph, store,
    chunk, embed, publish."""
    settings = get_settings()
    started = time.perf_counter()
    engine = create_db_engine(settings.database_url)
    try:
        result = run_build(url, settings, engine=engine, commit=commit, keep_workdir=keep_workdir)
        info = GraphStore(engine).snapshot(result.snapshot_id)
    except (IngestError, StoreError) as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    except OperationalError as error:
        _connection_failed(settings.database_url, error)
    finally:
        engine.dispose()
    for line in _build_summary(result, info, time.perf_counter() - started):
        typer.echo(line)


def _build_summary(result: BuildResult, info: SnapshotInfo, elapsed: float) -> list[str]:
    stats: dict[str, Any] = dict(info.stats)
    graph: dict[str, Any] = stats.get("graph", {})
    blobs: dict[str, Any] = stats.get("blobs", {})
    nodes = sum(graph.get("nodes_by_kind", {}).values())
    edges = sum(graph.get("edges_by_kind", {}).values())
    how = "reused: already built with this configuration" if result.reused else "built"
    steps = ", ".join(f"{name} {seconds:.1f} s" for name, seconds in result.timings.items())
    files = stats.get("inventory", {}).get("indexed", 0)
    return [
        f"Snapshot: {result.snapshot_id} ({info.status}, {how})",
        f"Repo:     github.com/{result.repo} @ {result.commit}",
        f"Graph:    {nodes:,} nodes, {edges:,} edges; "
        f"{graph.get('unresolved_calls', 0):,} unresolved calls",
        f"Text:     {files:,} files, {blobs.get('needed', 0):,} distinct blobs: "
        f"{blobs.get('stored', 0):,} new, {blobs.get('shared', 0):,} already stored",
        f"Search:   {_search_summary(stats)}",
        f"Digest:   {stats.get('graph_digest', '')}",
        f"Config:   {result.fingerprint}",
        f"Steps:    {steps}",
        f"Time:     {elapsed:.1f} s",
    ]


def _search_summary(stats: dict[str, Any]) -> str:
    chunks: dict[str, Any] = stats.get("chunks", {})
    embedded: dict[str, Any] = stats.get("embeddings", {})
    if not chunks:
        return "no chunks"
    text = f"{chunks.get('count', 0):,} chunks"
    if embedded:
        text += (
            f"; embeddings {embedded.get('model')} ({embedded.get('dim')} dims): "
            f"{embedded.get('embedded', 0):,} new, {embedded.get('cached', 0):,} cached"
        )
    return text


SnapshotOption = Annotated[
    int, typer.Option("--snapshot", "-s", help="Snapshot ID (repoatlas build prints it).")
]
EdgeKindsOption = Annotated[
    list[str] | None,
    typer.Option("--kinds", "-k", help="Edge kinds, comma-separated or repeated."),
]
DepthOption = Annotated[int, typer.Option(help="How many hops to follow.")]


def _split(values: Sequence[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


QueryResult = BaseModel | Sequence[BaseModel] | None


def _query(read: Callable[[GraphStore], QueryResult], missing: str = "Not found.") -> None:
    """Run one read against the configured database and print the result as JSON."""
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    try:
        result = read(GraphStore.from_settings(settings, engine))
    except StoreError as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    except OperationalError as error:
        _connection_failed(settings.database_url, error)
    finally:
        engine.dispose()
    if result is None:
        typer.secho(missing, err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if isinstance(result, BaseModel):
        _echo_json(pretty_json(result))
    else:
        _echo_json(pretty_json([item.model_dump(mode="json") for item in result]))


EXPLAIN_ROWS = 15  # rows per step that --explain prints


@app.command("search")
def search_command(
    query: Annotated[str, typer.Argument(help="What to look for, in words or identifiers.")],
    snapshot: SnapshotOption,
    mode: Annotated[
        str, typer.Option(help="vector, keyword, or hybrid (vector + keyword + graph).")
    ] = "hybrid",
    explain: Annotated[
        bool, typer.Option("--explain", help="Print every step's candidates and scores.")
    ] = False,
    budget: Annotated[int, typer.Option(help="Token budget for the evidence.")] = 12_000,
    k: Annotated[int, typer.Option("-k", "--k", help="At most this many results.")] = 20,
    code: Annotated[bool, typer.Option("--code", help="Print each result's code.")] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full result (with debug) as JSON.")
    ] = False,
) -> None:
    """Find the code for a question in a stored snapshot (no LLM involved)."""
    from repoatlas.retrieval.retriever import MODES, Retriever

    if mode not in MODES:
        typer.secho(f"Error: --mode must be one of {', '.join(MODES)}.", err=True, fg="red")
        raise typer.Exit(code=2)
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    started = time.perf_counter()
    try:
        retriever = Retriever.from_settings(settings, engine)
        result = retriever.retrieve(
            snapshot, query, mode=cast(Any, mode), budget_tokens=budget, k=k
        )
    except (StoreError, ValueError) as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    except OperationalError as error:
        _connection_failed(settings.database_url, error)
    finally:
        engine.dispose()
    elapsed = time.perf_counter() - started
    if as_json:
        _echo_json(pretty_json(result.model_dump(mode="json")))
        return
    for line in _search_lines(result, snapshot, elapsed, explain=explain, code=code):
        typer.echo(line)


DEFAULT_SMOKE = Path("eval") / "smoke" / "questions.yaml"


def _find_upward(relative: Path) -> Path:
    """``relative`` under the working directory or the nearest parent that has it."""
    for directory in (Path.cwd(), *Path.cwd().parents):
        if (directory / relative).is_file():
            return directory / relative
    return relative


@app.command("ask-smoke")
def ask_smoke_command(
    snapshot: SnapshotOption,
    max_cost: Annotated[
        float,
        typer.Option("--max-cost", min=0.01, help="Stop before the total could pass this (USD)."),
    ],
    questions: Annotated[
        Path | None, typer.Option(help="The question set (default: eval/smoke/questions.yaml).")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Write the summary JSON here (default: eval/smoke/results/...)."),
    ] = None,
) -> None:
    """Answer the smoke questions live on a snapshot (costs money; capped by --max-cost)."""
    import anyio

    from repoatlas.answers.factory import oneshot_from_settings
    from repoatlas.answers.prompting import SYSTEM_PROMPT, estimate_tokens
    from repoatlas.answers.schema import Budget
    from repoatlas.answers.smoke import SmokeItem, load_questions, run_smoke, summary_lines
    from repoatlas.answers.system import REPAIR_FACTOR
    from repoatlas.llm.client import LLMNotConfigured
    from repoatlas.llm.pricing import PriceTable

    settings = get_settings()
    configure(settings)
    path = questions or _find_upward(DEFAULT_SMOKE)
    smoke = load_questions(path)
    engine = create_db_engine(settings.database_url)
    try:
        store = GraphStore.from_settings(settings, engine)
        info = store.snapshot(snapshot)
        repo = f"{info.repo.owner}/{info.repo.name}"
        if repo != smoke.repo or not info.commit_sha.startswith(smoke.commit):
            typer.secho(
                f"Error: snapshot {snapshot} is {repo} @ {info.commit_sha[:7]}, but the questions "
                f"are for {smoke.repo} @ {smoke.commit[:7]}.",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)
        try:
            system = oneshot_from_settings(settings, engine, store)
        except LLMNotConfigured as error:
            typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=2) from None
        budget = Budget(
            max_cost_usd=settings.answer_max_cost_usd,
            max_output_tokens=settings.answer_max_output_tokens,
        )
        # The most one answer can cost: the evidence budget and the system prompt, uncached,
        # with every output token used, twice (the repair attempt).
        input_tokens = settings.answer_evidence_tokens + estimate_tokens(SYSTEM_PROMPT) + 500
        worst = REPAIR_FACTOR * PriceTable(settings.llm_prices).max_cost(
            settings.llm_large_model, input_tokens, budget.max_output_tokens
        )

        def progress(item: SmokeItem) -> None:
            kind = f"/{item.refusal_kind}" if item.refusal_kind else ""
            typer.echo(f"{item.id}: {item.status}{kind} ${item.cost_usd:.4f} {item.latency_s:.1f}s")

        summary = anyio.run(
            lambda: run_smoke(
                system,
                smoke,
                snapshot_id=snapshot,
                engine=engine,
                max_cost_usd=max_cost,
                budget=budget,
                worst_case_usd=worst,
                progress=progress,
            )
        )
    except StoreError as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    except OperationalError as error:
        _connection_failed(settings.database_url, error)
    finally:
        engine.dispose()
    target = out or path.parent / "results" / f"{time.strftime('%Y-%m-%d')}-snapshot{snapshot}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(target, pretty_json(summary.model_dump(mode="json")))
    typer.echo("")
    for line in summary_lines(summary):
        typer.echo(line)
    typer.echo("")
    typer.echo(f"Summary written to {target}.")


def _search_lines(
    result: Any, snapshot: int, elapsed: float, *, explain: bool, code: bool
) -> list[str]:
    debug = result.debug
    model = f" | model {debug.model}" if debug.model else ""
    lines = [
        f"Snapshot {snapshot} | {debug.mode}{model} | {len(result.evidence)} results, "
        f"{debug.tokens:,} of {debug.budget_tokens:,} tokens | {elapsed * 1000:.0f} ms",
    ]
    lines.extend(f"Note: {note}" for note in debug.notes)
    if not result.evidence:
        lines.append("Nothing found.")
    for number, item in enumerate(result.evidence, start=1):
        sources = ", ".join(sorted(item.sources))
        lines.append(
            f"{number:>3}. {item.score:.4f}  {item.ref.to_compact()}  {item.node_key}  [{sources}]"
        )
        if code:
            lines.extend(f"       {line}" for line in item.text.splitlines())
    if explain:
        lines.extend(_explain_lines(debug))
    return lines


def _explain_lines(debug: Any) -> list[str]:
    def rows(title: str, items: Sequence[Any], render: Callable[[Any], str]) -> list[str]:
        out = ["", f"{title} ({len(items)})"]
        out.extend(f"  {render(item)}" for item in items[:EXPLAIN_ROWS])
        if len(items) > EXPLAIN_ROWS:
            out.append(f"  ... {len(items) - EXPLAIN_ROWS} more")
        return out

    lines = ["", f"Keyword query: {debug.keyword_query or '-'}"]
    if debug.mode != "keyword":
        lines += rows(
            "Vector search (cosine similarity)",
            debug.vector,
            lambda h: f"{h.rank:>3}  {h.score:.4f}  {h.ref}  {h.node_key}",
        )
    if debug.mode != "vector":
        lines += rows(
            "Keyword search (ts_rank_cd)",
            debug.keyword,
            lambda h: f"{h.rank:>3}  {h.score:.4f}  {h.ref}  {h.node_key}",
        )
    if debug.mode == "hybrid":
        lines += rows(
            "Fused (reciprocal rank fusion, 1 / (60 + rank))",
            debug.fused,
            lambda h: f"{h.score:.4f}  {_ranks(h.ranks)}  {h.ref}  {h.node_key}",
        )
        lines += rows("Seeds", debug.seeds, lambda key: key)
        lines += rows(
            "Graph expansion (seed x confidence x 0.5^hops)",
            debug.expanded,
            lambda h: (
                f"{h.score:.4f}  {h.hops} hop(s), {h.via} {h.direction}, "
                f"confidence {h.confidence:.2f}, from {h.seed}  {h.node_key}"
            ),
        )
    lines += rows(
        "Ranked (max(fused, expanded) + name bonus)",
        debug.ranked,
        lambda h: (
            f"{h.score:.4f}  fused {h.fused:.4f}  expanded {h.expanded:.4f}  "
            f"bonus {h.bonus:.4f}  {'selected' if h.selected else 'skipped: ' + str(h.skipped)}  "
            f"{h.ref}"
        ),
    )
    timings = ", ".join(f"{step} {ms:.1f} ms" for step, ms in debug.timings_ms.items())
    lines += ["", f"Timings: {timings}"]
    return lines


def _ranks(ranks: dict[str, int]) -> str:
    return " ".join(f"{source[0]}={rank}" for source, rank in sorted(ranks.items()))


@query_app.command("snapshot")
def query_snapshot(snapshot: SnapshotOption) -> None:
    """A snapshot's repo, commit, status, stats, and error."""
    _query(lambda store: store.snapshot(snapshot))


@query_app.command("node")
def query_node(
    key: Annotated[str, typer.Argument(help="Symbol key.")], snapshot: SnapshotOption
) -> None:
    """One node by symbol key."""
    _query(lambda store: store.get_node(snapshot, key), f"Snapshot {snapshot} has no node {key!r}.")


@query_app.command("search")
def query_search(
    query: Annotated[str, typer.Argument(help="Name, qualified name, or key to look for.")],
    snapshot: SnapshotOption,
    kinds: Annotated[
        list[str] | None,
        typer.Option("--kinds", "-k", help="Node kinds, comma-separated or repeated."),
    ] = None,
    limit: Annotated[int, typer.Option(help="At most this many results.")] = 20,
) -> None:
    """Symbols by exact name, prefix, or trigram similarity."""
    _query(lambda store: store.find_symbols(snapshot, query, _split(kinds), limit))


@query_app.command("locate")
def query_locate(
    path: Annotated[str, typer.Argument(help="Repo-relative file path.")],
    line: Annotated[int, typer.Argument(help="1-based line number.")],
    snapshot: SnapshotOption,
) -> None:
    """The innermost symbol containing a line (or the file)."""
    _query(lambda store: store.locate(snapshot, path, line))


@query_app.command("callers")
def query_callers(
    key: Annotated[str, typer.Argument(help="Symbol key.")],
    snapshot: SnapshotOption,
    depth: DepthOption = 1,
) -> None:
    """Who calls a symbol, up to DEPTH calls away."""
    _query(lambda store: store.callers(snapshot, key, depth))


@query_app.command("callees")
def query_callees(
    key: Annotated[str, typer.Argument(help="Symbol key.")],
    snapshot: SnapshotOption,
    depth: DepthOption = 1,
) -> None:
    """What a symbol calls, up to DEPTH calls away."""
    _query(lambda store: store.callees(snapshot, key, depth))


@query_app.command("neighbors")
def query_neighbors(
    key: Annotated[str, typer.Argument(help="Symbol key.")],
    snapshot: SnapshotOption,
    direction: Annotated[str, typer.Option(help="in, out, or both.")] = "out",
    kinds: EdgeKindsOption = None,
    depth: DepthOption = 1,
) -> None:
    """Nodes linked to a symbol by edges of the given kinds (default: all)."""
    chosen = cast(Direction, direction)  # the store rejects anything else
    _query(lambda store: store.neighbors(snapshot, key, chosen, _split(kinds), depth))


@query_app.command("path")
def query_path(
    src: Annotated[str, typer.Argument(help="Symbol key to start from.")],
    dst: Annotated[str, typer.Argument(help="Symbol key to reach.")],
    snapshot: SnapshotOption,
    kinds: EdgeKindsOption = None,
) -> None:
    """A shortest path (default kinds: calls and imports)."""
    _query(
        lambda store: store.shortest_path(snapshot, src, dst, _split(kinds)),
        f"No path between {src!r} and {dst!r}.",
    )


@query_app.command("subgraph")
def query_subgraph(
    snapshot: SnapshotOption,
    parent: Annotated[str, typer.Argument(help="Directory, file, or symbol key.")] = "dir:.",
    level: Annotated[str, typer.Option(help="dir, file, or symbol.")] = "dir",
) -> None:
    """A container's children and the aggregated edges between them."""
    chosen = cast(Level, level)  # the store rejects anything else
    _query(lambda store: store.subgraph(snapshot, parent, chosen))


@query_app.command("code")
def query_code(
    path: Annotated[str, typer.Argument(help="Repo-relative file path.")],
    start: Annotated[int, typer.Argument(help="First line (1-based).")],
    end: Annotated[int, typer.Argument(help="Last line (inclusive).")],
    snapshot: SnapshotOption,
) -> None:
    """Lines of a file in the snapshot, from the stored text."""
    _query(lambda store: store.read_code(snapshot, path, start, end))


@query_app.command("owners")
def query_owners(
    path: Annotated[str, typer.Argument(help="Repo-relative file path.")],
    snapshot: SnapshotOption,
) -> None:
    """A file's authors by commit count."""
    _query(lambda store: store.owners(snapshot, path))


@query_app.command("unresolved")
def query_unresolved(
    name: Annotated[str, typer.Argument(help="Callee name (append) or text (self.items.append).")],
    snapshot: SnapshotOption,
) -> None:
    """Call sites with no edge whose callee has this name."""
    _query(lambda store: store.unresolved_calls(snapshot, name))


STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK")  # SIGBREAK: Ctrl+Break, Windows only


def _install_stop_signals(worker: Worker) -> dict[int, Any]:
    """First signal: stop gracefully. A second one: stop now (``KeyboardInterrupt``)."""
    presses = 0

    def on_signal(_signum: int, _frame: FrameType | None) -> None:
        nonlocal presses
        presses += 1
        if presses > 1:
            raise KeyboardInterrupt
        worker.request_stop()
        typer.echo("Stopping after the current stage. Press Ctrl+C again to stop now.", err=True)

    previous: dict[int, Any] = {}
    for name in STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is not None:
            previous[number] = signal.signal(number, on_signal)
    return previous


WORKER_HELP = (
    "Run background jobs (snapshot builds) until stopped.\n\n"
    "Ctrl+C (or Ctrl+Break, or SIGTERM) stops the worker gracefully: the current job stops "
    "after its current stage and goes back to the queue. Creating or touching the stop file "
    "does the same, which is the portable way to stop a worker that has no console. Press "
    "Ctrl+C twice to stop at once; another worker resumes the job after its lease runs out."
)


@app.command("worker", help=WORKER_HELP)
def worker_command(
    once: Annotated[bool, typer.Option(help="Process at most one due job, then exit.")] = False,
    stop_file: Annotated[
        Path | None,
        typer.Option(
            help="Stop gracefully when this file appears or changes "
            "(default: worker.stop in the data directory)."
        ),
    ] = None,
    worker_id: Annotated[
        str | None, typer.Option("--id", help="Name in the jobs table (default: host:pid:random).")
    ] = None,
) -> None:
    settings = get_settings()
    path = stop_file or settings.data_dir / "worker.stop"
    worker = Worker(settings, stop_file=path, worker_id=worker_id)
    kinds = ", ".join(worker.kinds) or "no"
    typer.echo(
        f"Worker {worker.id}: running {kinds} jobs. Stop with Ctrl+C, or create or touch {path}.",
        err=True,
    )
    previous = _install_stop_signals(worker)
    try:
        if once:
            worker.run_once()
            worker.close()
        else:
            worker.run()
    except KeyboardInterrupt:
        worker.close()
        typer.echo("Stopped at once; the job goes back to the queue.", err=True)
        raise typer.Exit(code=130) from None
    except OperationalError as error:
        worker.close()
        _connection_failed(settings.database_url, error)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    typer.echo(f"Worker {worker.id} stopped after {worker.processed} job(s).", err=True)


@api_app.command("openapi")
def api_openapi(
    out: Annotated[
        str, typer.Option(help="Where to write the spec ('-' = stdout).")
    ] = "openapi.json",
    check: Annotated[
        bool, typer.Option(help="Write nothing; exit 1 if the file is not the current spec.")
    ] = False,
) -> None:
    """Export the OpenAPI spec (commit it as backend/openapi.json)."""
    from repoatlas.api.openapi import render_spec  # the app is only needed here

    text = render_spec()
    if out == "-":
        _echo_json(text)
        return
    path = Path(out)
    if check:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            typer.secho(
                f"{path} is out of date. Run: uv run repoatlas api openapi --out {out} "
                "(then scripts/gen_api_types.sh or .ps1 for the web app's types).",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        typer.echo(f"{path} is up to date.")
        return
    write_text_lf(path, text)
    typer.echo(f"Wrote {path}.")
