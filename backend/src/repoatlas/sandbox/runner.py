"""``SandboxRunner``: test suites and indexers in hardened containers (SBX-1, SBX-6, SBX-9)."""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from repoatlas.sandbox.docker import remove_image
from repoatlas.sandbox.hardened import DEFAULT_LIMITS, Limits, SandboxRun, run_hardened
from repoatlas.sandbox.images import (
    IMAGE_PREFIX,
    SANDBOX_UID,
    SOURCE_MTIME,
    ImageInfo,
    build_from_context,
)
from repoatlas.sandbox.recipe import Recipe
from repoatlas.sandbox.results import ReportError, TestResult, parse_report

TestId = str
WORK_DIR = "/tmp/work"


class Mutation(BaseModel):
    """One mutated file: ``content`` replaces ``path`` in the repo image's source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol_key: str
    path: str
    kind: str
    content: bytes


@dataclass(slots=True)
class TestRun:
    __test__ = False

    results: list[TestResult]
    duration_s: float
    exit_code: int | None
    log_tail: str
    timed_out: bool = False
    report_error: str | None = None
    selected: list[str] = field(default_factory=list)

    @property
    def collection_errors(self) -> list[TestResult]:
        return [r for r in self.results if r.collection]

    def outcomes(self) -> dict[str, str]:
        return {r.test_id: r.outcome for r in self.results if not r.collection}


def _shell_env(env: dict[str, str]) -> str:
    if not env:
        return ""
    return "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())) + "\n"


def enter_workdir(recipe: Recipe) -> str:
    """Shell lines that enter the suite's working directory."""
    if recipe.workdir == "src":
        return "cd /src\n"
    # A writable copy (node_modules stays in the image and is linked, not copied).
    return (
        f"mkdir -p {WORK_DIR} && cd /src && "
        f"tar --exclude=./node_modules -cf - . | tar -C {WORK_DIR} -xf - && "
        f"if [ -d node_modules ]; then ln -s /src/node_modules {WORK_DIR}/node_modules; fi\n"
        f"cd {WORK_DIR}\n"
    )


def selection_args(recipe: Recipe, only: Sequence[TestId]) -> list[str]:
    """Arguments that limit a run to ``only``: pytest node IDs, or the files that hold them."""
    if recipe.test.selection == "nodeids":
        return sorted(set(only))
    files = {test_id.split(" > ", 1)[0].split("::", 1)[0] for test_id in only}
    return sorted(files)


def suite_script(recipe: Recipe, only: Sequence[TestId] | None = None) -> str:
    cmd = recipe.test.cmd
    if only:
        cmd += " " + " ".join(shlex.quote(arg) for arg in selection_args(recipe, only))
    return enter_workdir(recipe) + _shell_env(recipe.env) + cmd


class SandboxRunner:
    """Runs one recipe's suite and indexer in its repo image."""

    def __init__(
        self,
        recipe: Recipe,
        image: ImageInfo | str,
        *,
        limits: Limits = DEFAULT_LIMITS,
        known_test_paths: Iterable[str] = (),
    ) -> None:
        self.recipe = recipe
        self.image = image.tag if isinstance(image, ImageInfo) else image
        self.limits = limits
        self.known_test_paths = list(known_test_paths)

    def mutated_image(self, mutation: Mutation) -> ImageInfo:
        """A layer on top of the repo image that replaces one source file (no network)."""
        import io
        import tarfile

        dockerfile = (
            f"FROM {self.image}\n"
            f"COPY --chown={SANDBOX_UID}:{SANDBOX_UID} src/{mutation.path} /src/{mutation.path}\n"
        )
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data, mode in (
                ("Dockerfile", dockerfile.encode(), 0o644),
                (f"src/{mutation.path}", mutation.content, 0o644),
            ):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), mode, SOURCE_MTIME
                tar.addfile(info, io.BytesIO(data))
        tag_hash = hashlib.sha256(mutation.content + mutation.symbol_key.encode()).hexdigest()
        tag = f"{IMAGE_PREFIX}/{self.recipe.id}-mut:{tag_hash[:16]}"
        return build_from_context(buffer.getvalue(), tag)

    def run_tests(
        self, *, mutation: Mutation | None = None, only: Sequence[TestId] | None = None
    ) -> TestRun:
        image = self.image
        mutated: ImageInfo | None = None
        if mutation is not None:
            mutated = self.mutated_image(mutation)
            image = mutated.tag
        try:
            run = run_hardened(
                image,
                suite_script(self.recipe, only),
                timeout_s=self.recipe.test.timeout_s,
                limits=self.limits,
                name_prefix=f"{IMAGE_PREFIX}-test-{self.recipe.id}",
            )
        finally:
            if mutated is not None:
                remove_image(mutated.tag)
        return self._test_run(run, list(only or []))

    def _test_run(self, run: SandboxRun, selected: list[str]) -> TestRun:
        report_name = self.recipe.test.report_path.removeprefix("/tmp/out/")
        results: list[TestResult] = []
        error: str | None = None
        data = run.files.get(report_name)
        if run.timed_out:
            error = f"timed out after {self.recipe.test.timeout_s} s"
        elif data is None:
            error = "the test command wrote no report"
        else:
            try:
                results = parse_report(
                    self.recipe.test.reporter, data, known_paths=self.known_test_paths
                )
            except ReportError as exc:
                error = str(exc)
        return TestRun(
            results=results,
            duration_s=run.duration_s,
            exit_code=run.exit_code,
            log_tail=run.log_tail(),
            timed_out=run.timed_out,
            report_error=error,
            selected=selected,
        )
