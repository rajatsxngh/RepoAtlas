"""Registries for evaluation (EVL-1): answer systems, impact systems, and question suites.

Systems are registered by name with a factory that builds them from an ``EvalContext``
(settings, database engine, LLM client, graph store), so ``repoatlas eval run --system <name>``
can build any registered system. Factories are called once per run.

Impact systems predict what a change affects. The contract is ``ImpactPrediction``: for one
``ImpactTarget`` (a symbol key or a unified diff), a ranked list of ``ImpactItem`` symbols with
a group (a test, a public API, a caller, or unknown), a score, and optional evidence.

Suites are named sets of questions with their own scorers: ``register_suite(name, loader,
scorers)``. ``loader(split)`` returns the questions (``EvalItem``) of a split; ``scorers`` maps a
question type to the function that scores one output for one question (``"*"`` matches any
type). The default suite, ``benchmark``, is registered by ``repoatlas.evals.suites``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repoatlas.retrieval.evidence import EvidenceRef

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from repoatlas.answers.system import AnswerSystem
    from repoatlas.config import Settings
    from repoatlas.evals.outputs import ItemScore, SystemOutput
    from repoatlas.llm.client import LLM
    from repoatlas.store.graphstore import GraphStore


class RegistryError(LookupError):
    """An unknown name, or a name registered twice."""


@dataclass
class EvalContext:
    """What a system factory may use. ``llm`` and ``engine`` are ``None`` in tests that don't
    need them; factories that need one raise if it is missing."""

    settings: Settings
    engine: Engine | None = None
    llm: LLM | None = None
    store: GraphStore | None = None
    options: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Answer systems
# ---------------------------------------------------------------------------

AnswerSystemFactory = Callable[[EvalContext], "AnswerSystem"]
_answer_systems: dict[str, AnswerSystemFactory] = {}


def register_answer_system(
    name: str, factory: AnswerSystemFactory, *, replace: bool = False
) -> None:
    if name in _answer_systems and not replace:
        raise RegistryError(f"An answer system named {name!r} is already registered.")
    _answer_systems[name] = factory


def get_answer_system(name: str) -> AnswerSystemFactory:
    _load_builtins()
    try:
        return _answer_systems[name]
    except KeyError:
        known = ", ".join(sorted(_answer_systems)) or "none"
        raise RegistryError(f"No answer system named {name!r} (registered: {known}).") from None


def answer_system_names() -> list[str]:
    _load_builtins()
    return sorted(_answer_systems)


# ---------------------------------------------------------------------------
# Impact systems
# ---------------------------------------------------------------------------

ImpactGroup = Literal["test", "public_api", "caller", "unknown"]


class ImpactTarget(BaseModel):
    """What changes: one symbol, or a unified diff. Exactly one is set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol_key: str | None = None
    diff: str | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.symbol_key is None) == (self.diff is None):
            raise ValueError("Set exactly one of symbol_key and diff.")
        return self


class ImpactItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol_key: str
    group: ImpactGroup
    score: float = Field(ge=0)
    path: str | None = None
    evidence: list[EvidenceRef] | None = None


class ImpactPrediction(BaseModel):
    """Items in rank order (highest score first; ties keep the system's order)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: ImpactTarget
    items: list[ImpactItem]
    cost_usd: float = 0.0

    def ranked(self) -> list[ImpactItem]:
        order = {id(item): index for index, item in enumerate(self.items)}
        return sorted(self.items, key=lambda item: (-item.score, order[id(item)]))


class ImpactSystem(Protocol):
    name: str

    async def predict(self, snapshot_id: int, target: ImpactTarget) -> ImpactPrediction: ...


ImpactSystemFactory = Callable[[EvalContext], ImpactSystem]
_impact_systems: dict[str, ImpactSystemFactory] = {}


def register_impact_system(
    name: str, factory: ImpactSystemFactory, *, replace: bool = False
) -> None:
    if name in _impact_systems and not replace:
        raise RegistryError(f"An impact system named {name!r} is already registered.")
    _impact_systems[name] = factory


def get_impact_system(name: str) -> ImpactSystemFactory:
    _load_builtins()
    try:
        return _impact_systems[name]
    except KeyError:
        known = ", ".join(sorted(_impact_systems)) or "none"
        raise RegistryError(f"No impact system named {name!r} (registered: {known}).") from None


def impact_system_names() -> list[str]:
    _load_builtins()
    return sorted(_impact_systems)


SystemKind = Literal["answer", "impact"]


def system_kind(name: str) -> SystemKind:
    """Which registry ``name`` is in (answer systems win a tie)."""
    _load_builtins()
    if name in _answer_systems:
        return "answer"
    if name in _impact_systems:
        return "impact"
    known = ", ".join(sorted({*_answer_systems, *_impact_systems})) or "none"
    raise RegistryError(f"No system named {name!r} (registered: {known}).")


# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------


class EvalItem(BaseModel):
    """One question of a suite. ``truth`` is the suite's own ground truth (a model or data)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    type: str
    text: str
    repo_id: str
    commit: str
    split: str
    truth: Any = None


ItemScorer = Callable[["EvalItem", "SystemOutput"], "ItemScore"]
SuiteLoader = Callable[[str], list[EvalItem]]


@dataclass(frozen=True)
class Suite:
    name: str
    loader: SuiteLoader
    scorers: Mapping[str, ItemScorer]
    info: Callable[[], dict[str, Any]] | None = None  # version, digests: recorded in reports

    def scorer_for(self, item_type: str) -> ItemScorer:
        scorer = self.scorers.get(item_type) or self.scorers.get("*")
        if scorer is None:
            raise RegistryError(f"Suite {self.name!r} has no scorer for {item_type!r} questions.")
        return scorer

    def load(self, split: str) -> list[EvalItem]:
        return self.loader(split)


_suites: dict[str, Suite] = {}


def register_suite(
    name: str,
    loader: SuiteLoader,
    scorers: Mapping[str, ItemScorer],
    *,
    info: Callable[[], dict[str, Any]] | None = None,
    replace: bool = False,
) -> Suite:
    if name in _suites and not replace:
        raise RegistryError(f"A suite named {name!r} is already registered.")
    if not scorers:
        raise ValueError("A suite needs at least one scorer.")
    suite = Suite(name=name, loader=loader, scorers=dict(scorers), info=info)
    _suites[name] = suite
    return suite


def get_suite(name: str) -> Suite:
    _load_builtins()
    try:
        return _suites[name]
    except KeyError:
        known = ", ".join(sorted(_suites)) or "none"
        raise RegistryError(f"No suite named {name!r} (registered: {known}).") from None


def suite_names() -> list[str]:
    _load_builtins()
    return sorted(_suites)


_builtins_loaded = False


def _load_builtins() -> None:
    """Register the built-in suite and baselines on first use (they import heavier modules)."""
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    import importlib

    for module in ("repoatlas.evals.suites", "repoatlas.evals.baselines"):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
