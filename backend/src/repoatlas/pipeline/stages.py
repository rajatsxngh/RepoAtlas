"""The stage registry and the config fingerprint (API-4, decision 0007).

A build is an ordered list of stages. Each ``Stage`` has a name, a version, the settings its
output depends on, a ``run(ctx)`` over the shared ``BuildContext``, and a weight (its share of
the build's progress). The built-in stages are ``fetch``, ``inventory``, ``parse``, ``graph``,
``store``, and ``publish``, with the retrieval stages (``chunk``, ``embed``) registered between
``store`` and ``publish``; new work registers more with ``register_stage(stage, after=…)``
and the job runner never changes.

The config fingerprint is a sha256 over the ordered stages: each one's name, version, component
versions, and the values of its settings. Registering, removing, reordering, or changing a stage
changes the fingerprint, so a snapshot (repo, commit, fingerprint) never mixes output of two
pipelines. Stages must be idempotent: run again on the same snapshot, a stage finds its output
(a checkpoint, or the stored snapshot) and skips the work, so a retried build resumes.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, SecretStr

from repoatlas.config import Settings
from repoatlas.pipeline import fingerprint
from repoatlas.pipeline.context import BuildContext

# Modules whose import registers the built-in stages, in order.
BUILTIN_STAGE_MODULES: tuple[str, ...] = ("repoatlas.pipeline.build", "repoatlas.retrieval.stages")


@dataclass(frozen=True)
class Stage:
    """One step of a build. ``settings_keys`` and ``components()`` (versions of the code it
    runs, such as parser packages) are what its output depends on; both enter the fingerprint,
    with ``version``. ``weight`` is the stage's share of the build's progress."""

    name: str
    version: int
    settings_keys: tuple[str, ...]
    run: Callable[[BuildContext], None]
    weight: float = 1.0
    components: Callable[[], dict[str, JsonValue]] = dict

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("A stage needs a name.")
        if self.version < 1:
            raise ValueError(f"Stage {self.name!r} needs a version of at least 1.")
        if self.weight <= 0:
            raise ValueError(f"Stage {self.name!r} needs a positive weight.")


_REGISTRY: list[Stage] = []
_loaded = False


def _load_builtins() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        for module in BUILTIN_STAGE_MODULES:
            importlib.import_module(module)


def register_stage(stage: Stage, after: str | None = None) -> None:
    """Add ``stage`` at the end of the pipeline, or right after the stage named ``after``."""
    _load_builtins()
    register_builtin(stage, after)


def register_builtin(stage: Stage, after: str | None = None) -> None:
    """``register_stage`` for the modules in ``BUILTIN_STAGE_MODULES``, which register while
    the built-ins load (each imports the modules whose stages it is placed after)."""
    if any(existing.name == stage.name for existing in _REGISTRY):
        raise ValueError(f"A stage named {stage.name!r} is already registered.")
    if after is None:
        _REGISTRY.append(stage)
        return
    for position, existing in enumerate(_REGISTRY):
        if existing.name == after:
            _REGISTRY.insert(position + 1, stage)
            return
    raise ValueError(f"No stage named {after!r} is registered.")


def unregister_stage(name: str) -> None:
    _load_builtins()
    _REGISTRY[:] = [stage for stage in _REGISTRY if stage.name != name]


def registered_stages() -> tuple[Stage, ...]:
    """The pipeline, in order."""
    _load_builtins()
    return tuple(_REGISTRY)


def _setting(settings: Settings, key: str) -> JsonValue:
    value = getattr(settings, key)
    if isinstance(value, SecretStr):
        raise ValueError(f"Secret setting {key!r} can't be part of a fingerprint.")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple):
        return list(value)
    if value is None or isinstance(value, str | int | float | bool | list | dict):
        return value
    raise TypeError(f"Setting {key!r} has no JSON form for the fingerprint.")


def fingerprint_parts(
    settings: Settings, stages: Sequence[Stage] | None = None
) -> dict[str, JsonValue]:
    """What a build's output depends on: every stage in order with its version, component
    versions, and the values of its settings."""
    chosen = registered_stages() if stages is None else stages
    return {
        "stages": [
            {
                "name": stage.name,
                "version": stage.version,
                "components": stage.components(),
                "settings": {key: _setting(settings, key) for key in stage.settings_keys},
            }
            for stage in chosen
        ]
    }


def config_fingerprint(settings: Settings, stages: Sequence[Stage] | None = None) -> str:
    """The fingerprint of the registered pipeline (or of ``stages``) under ``settings``."""
    return fingerprint.compute(fingerprint_parts(settings, stages))


def run_stages(
    ctx: BuildContext,
    stages: Sequence[Stage] | None = None,
    *,
    report: Callable[[str, float], None] | None = None,
    check: Callable[[], None] | None = None,
) -> None:
    """Run ``stages`` (default: the registered ones) in order on ``ctx``.

    ``report(stage, progress)`` receives the build's progress: the weights of the finished
    stages plus the current stage's reported share of its weight, over the total weight.
    ``check()`` runs before every stage; raising there (a stopping worker) ends the build
    between stages. Each stage's run time goes into ``ctx.timings``.
    """
    chosen = list(registered_stages() if stages is None else stages)
    total = sum(stage.weight for stage in chosen) or 1.0
    finished = 0.0
    try:
        for stage in chosen:
            if check is not None:
                check()
            ctx.on_progress = _reporter(report, stage, finished, total)
            ctx.progress(0.0)
            started = time.perf_counter()
            stage.run(ctx)
            ctx.timings[stage.name] = round(time.perf_counter() - started, 3)
            ctx.progress(1.0)
            finished += stage.weight
    finally:
        ctx.on_progress = None


def _reporter(
    report: Callable[[str, float], None] | None, stage: Stage, finished: float, total: float
) -> Callable[[float], None] | None:
    if report is None:
        return None

    def on_progress(fraction: float) -> None:
        report(stage.name, (finished + fraction * stage.weight) / total)

    return on_progress
