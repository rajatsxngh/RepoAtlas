"""``repoatlas sandbox …``: build, baseline, index, and mutate the curated benchmark repos."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from repoatlas.sandbox.images import RepoImageInfo
    from repoatlas.sandbox.recipe import Recipe

RecipeArg = Annotated[Path, typer.Argument(help="Recipe file, e.g. sandbox/recipes/click.yaml.")]

sandbox_app = typer.Typer(
    help="Hardened sandbox for the curated benchmark repos (decision 0012).", no_args_is_help=True
)


@sandbox_app.command("proxy")
def proxy_command(
    down: Annotated[
        bool, typer.Option("--down", help="Stop the proxy and remove the network.")
    ] = False,
) -> None:
    """Start (or stop) the internal build network and its allowlist proxy."""
    from repoatlas.sandbox.network import (
        ALLOWED_HOSTS,
        INTERNAL_NETWORK,
        PROXY_URL,
        ensure_proxy,
        proxy_down,
    )

    if down:
        proxy_down()
        typer.echo("Proxy stopped and network removed.")
        return
    image = ensure_proxy()
    typer.echo(f"Network {INTERNAL_NETWORK} (internal) with proxy {PROXY_URL} ({image[:19]}).")
    typer.echo("Allowed: " + ", ".join(ALLOWED_HOSTS))


def _load(path: Path) -> Recipe:
    from repoatlas.sandbox.recipe import RecipeError, load_recipe

    try:
        return load_recipe(path)
    except (RecipeError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@sandbox_app.command("build")
def build_command(recipe_path: RecipeArg) -> None:
    """Build the repo image: source from git objects, installs through the proxy."""
    from repoatlas.sandbox.artifacts import IMAGE_JSON, write_model
    from repoatlas.sandbox.images import build_image

    recipe = _load(recipe_path)
    info = build_image(recipe)
    path = write_model(recipe, IMAGE_JSON, info)
    typer.echo(f"{info.image.tag} {info.image.digest}")
    typer.echo(f"{info.files} files; install took {info.install_seconds} s")
    typer.echo(f"Wrote {path}")
    _manifest(recipe)


def _image(recipe: Recipe) -> RepoImageInfo:
    """The recorded repo image, which must still exist locally with the recorded digest."""
    from repoatlas.sandbox.artifacts import IMAGE_JSON, read_model
    from repoatlas.sandbox.docker import image_id
    from repoatlas.sandbox.images import RepoImageInfo

    info = read_model(recipe, IMAGE_JSON, RepoImageInfo)
    if info is None or image_id(info.image.tag) != info.image.digest:
        typer.echo(f"No current image for {recipe.id}; run: repoatlas sandbox build", err=True)
        raise typer.Exit(1)
    return info


def _image_tag(recipe: Recipe) -> str:
    return _image(recipe).image.tag


@sandbox_app.command("test")
def test_command(recipe_path: RecipeArg) -> None:
    """Run the full suite once and print a summary (a smoke check of the recipe)."""
    from collections import Counter

    from repoatlas.sandbox.runner import SandboxRunner

    recipe = _load(recipe_path)
    run = SandboxRunner(recipe, _image_tag(recipe)).run_tests()
    counts = Counter(run.outcomes().values())
    typer.echo(
        f"exit {run.exit_code} in {run.duration_s} s; "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    )
    typer.echo(f"collection errors: {len(run.collection_errors)}")
    if run.report_error:
        typer.echo(f"report: {run.report_error}")
        typer.echo(run.log_tail)
        raise typer.Exit(1)


@sandbox_app.command("baseline")
def baseline_command(
    recipe_path: RecipeArg,
    runs: Annotated[int, typer.Option(help="Full-suite runs.")] = 3,
    check: Annotated[
        bool, typer.Option("--check", help="Run once more and compare with the saved baseline.")
    ] = False,
) -> None:
    """Run the suite several times; quarantine flaky tests and record pre-failing ones."""
    from repoatlas.sandbox.artifacts import BASELINE_JSON, read_model, write_model
    from repoatlas.sandbox.baseline import Baseline, check_baseline, run_baseline
    from repoatlas.sandbox.recipe import write_quarantine
    from repoatlas.sandbox.runner import SandboxRunner, TestRun

    recipe = _load(recipe_path)
    image = _image(recipe)
    runner = SandboxRunner(recipe, image.image)
    if check:
        saved = read_model(recipe, BASELINE_JSON, Baseline)
        if saved is None or saved.image_digest != image.image.digest:
            typer.echo("No baseline for the current image; run without --check first.", err=True)
            raise typer.Exit(1)
        checked = check_baseline(runner, saved)
        write_model(recipe, BASELINE_JSON, checked)
        assert checked.check is not None
        result = checked.check
        typer.echo(
            f"Check run ({result.duration_s} s): {result.compared} stable tests compared, "
            f"{len(result.mismatches)} mismatches"
        )
        for test_id, change in list(result.mismatches.items())[:20]:
            typer.echo(f"  {test_id}: {change}")
        _manifest(recipe)
        if result.mismatches:
            raise typer.Exit(1)
        return

    def progress(index: int, run: TestRun) -> None:
        typer.echo(f"run {index}/{runs}: {len(run.results)} results in {run.duration_s} s")

    baseline = run_baseline(runner, image_digest=image.image.digest, runs=runs, progress=progress)
    path = write_model(recipe, BASELINE_JSON, baseline)
    write_quarantine(recipe_path, baseline.recipe_quarantine())
    typer.echo(
        f"{len(baseline.outcomes)} stable tests, {len(baseline.quarantined)} quarantined, "
        f"{len(baseline.pre_failing)} pre-failing, "
        f"{len(baseline.collection_errors)} suite-level errors"
    )
    typer.echo(f"Wrote {path} and the quarantine list in {recipe_path}")
    _manifest(recipe)


def _manifest(recipe: Recipe) -> None:
    """Rewrite manifest.json: every artifact's sha256, the recipe hash, and tool versions."""
    from repoatlas import __version__
    from repoatlas.sandbox.artifacts import IMAGE_JSON, SCIP_JSON, read_model, write_manifest
    from repoatlas.sandbox.docker import docker
    from repoatlas.sandbox.images import RepoImageInfo
    from repoatlas.sandbox.runner import ScipArtifact

    tools = {"repoatlas": __version__}
    server = docker(["version", "--format", "{{.Server.Version}}"], check=False)
    if server.returncode == 0:
        tools["docker"] = server.text
    image = read_model(recipe, IMAGE_JSON, RepoImageInfo)
    if image is not None:
        tools["base_image"] = f"{image.base.tag}@{image.base.digest}"
        tools["repo_image"] = f"{image.image.tag}@{image.image.digest}"
        for ref in image.base.upstream:
            tools[f"upstream:{ref.split('@')[0]}"] = ref.split("@")[-1]
    scip = read_model(recipe, SCIP_JSON, ScipArtifact)
    if scip is not None:
        tools[scip.indexer] = scip.version
    path = write_manifest(recipe, tools)
    typer.echo(f"Updated {path}")


@sandbox_app.command("scip")
def scip_command(recipe_path: RecipeArg) -> None:
    """Build the SCIP index inside the repo image (network off)."""
    from repoatlas.sandbox.artifacts import SCIP_JSON, recipe_dir, write_model
    from repoatlas.sandbox.runner import SandboxRunner
    from repoatlas.sandbox.scip import read_index

    recipe = _load(recipe_path)
    runner = SandboxRunner(recipe, _image_tag(recipe))
    artifact, data = runner.run_scip()
    index = read_index(data)
    occurrences = sum(len(document.occurrences) for document in index.documents)
    (recipe_dir(recipe) / artifact.path).write_bytes(data)
    write_model(recipe, SCIP_JSON, artifact)
    typer.echo(
        f"{artifact.indexer} {artifact.version}: {len(index.documents)} documents, "
        f"{occurrences} occurrences, {artifact.size} bytes in {artifact.duration_s} s"
    )
    _manifest(recipe)


@sandbox_app.command("mutate")
def mutate_command(
    recipe_path: RecipeArg,
    targets: Annotated[int, typer.Option(help="Targets to mutate (a stratified sample).")] = 30,
    seed: Annotated[int, typer.Option(help="Sampling seed (recorded in every record).")] = 7,
    resume: Annotated[
        bool, typer.Option("--resume", help="Keep mutations.jsonl and skip finished targets.")
    ] = False,
) -> None:
    """Mutate sampled functions one at a time and record the confirmed failing tests."""
    from collections import Counter

    from repoatlas.sandbox.artifacts import (
        BASELINE_JSON,
        MUTATIONS_JSONL,
        SCIP_INDEX,
        SCIP_JSON,
        read_model,
        recipe_dir,
        sha256_file,
    )
    from repoatlas.sandbox.baseline import Baseline
    from repoatlas.sandbox.images import open_source
    from repoatlas.sandbox.mutations import MutationRecord, read_records, run_mutations
    from repoatlas.sandbox.parsed import parse_tree
    from repoatlas.sandbox.runner import SandboxRunner, ScipArtifact
    from repoatlas.sandbox.scip import read_index
    from repoatlas.sandbox.targets import candidates, ordered_sample

    recipe = _load(recipe_path)
    image = _image(recipe)
    folder = recipe_dir(recipe)
    baseline = read_model(recipe, BASELINE_JSON, Baseline)
    if baseline is None or baseline.image_digest != image.image.digest:
        typer.echo("No baseline for the current image; run: repoatlas sandbox baseline", err=True)
        raise typer.Exit(1)
    scip = read_model(recipe, SCIP_JSON, ScipArtifact)
    index_path = folder / SCIP_INDEX
    if scip is None or not index_path.is_file() or sha256_file(index_path) != scip.sha256:
        typer.echo("No SCIP index matching scip.json; run: repoatlas sandbox scip", err=True)
        raise typer.Exit(1)
    out = folder / MUTATIONS_JSONL
    existing = read_records(out)
    if existing and not resume:
        out.unlink()
    elif any(record.seed != seed for record in existing):
        typer.echo("mutations.jsonl was sampled with another seed; drop --resume.", err=True)
        raise typer.Exit(1)

    index = read_index(index_path.read_bytes())
    with open_source(recipe) as tree:
        parsed = parse_tree(tree)
        found = candidates(index, parsed)
        chosen = ordered_sample(found, seed)[:targets]
        typer.echo(f"{len(found)} reachable mutable functions; mutating {len(chosen)}")

        def progress(record: MutationRecord) -> None:
            typer.echo(
                f"  {record.status:<11} {len(record.failing):>4} failing  "
                f"{record.runtime_s:>6} s  {record.symbol_key}"
            )

        records = run_mutations(
            SandboxRunner(recipe, image.image),
            chosen,
            tree=tree,
            parsed=parsed,
            baseline=baseline,
            seed=seed,
            out=out,
            progress=progress,
        )
    statuses = Counter(record.status for record in records)
    with_failures = [record for record in records if record.has_failures]
    failing = [test for record in with_failures for test in record.failing]
    unmapped = sum(1 for test in failing if test.symbol_key is None)
    typer.echo(", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    typer.echo(
        f"{len(with_failures)} targets with confirmed failures; {len(failing)} failing tests "
        f"({unmapped} unmapped); {sum(r.runtime_s for r in records):.0f} s in total"
    )
    _manifest(recipe)


@sandbox_app.command("manifest")
def manifest_command(recipe_path: RecipeArg) -> None:
    """Rewrite manifest.json from the artifacts on disk (after editing a recipe by hand)."""
    _manifest(_load(recipe_path))
