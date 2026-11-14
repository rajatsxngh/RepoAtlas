"""Agent fixtures: the small fixture repositories built into snapshots through the pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine

from repoatlas.agent.tools import Toolbox
from repoatlas.config import Settings
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.store_data import StoreImpactData
from repoatlas.pipeline.local import build_local_snapshot
from repoatlas.retrieval.embed import embedder_for
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from tests.integration.conftest import empty_store
from tests.unit.api.helpers import api_settings

REPOS = Path(__file__).resolve().parents[2] / "fixtures" / "repos"


@dataclass
class Built:
    """One fixture repository as a ready snapshot."""

    settings: Settings
    engine: Engine
    store: GraphStore
    snapshot_id: int
    commit: str

    def toolbox(self) -> Toolbox:
        return Toolbox(
            self.snapshot_id,
            self.store,
            Retriever(self.store, embedder_for(self.settings)),
            ImpactEngine(StoreImpactData(self.store)),
        )


def build_fixture(name: str, settings: Settings, engine: Engine) -> Built:
    built = build_local_snapshot(REPOS / name, settings, engine, name=name)
    store = GraphStore.from_settings(settings, engine)
    return Built(settings, engine, store, built.snapshot_id, built.commit)


@pytest.fixture(scope="module")
def agent_settings(pg_url: str, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return api_settings(
        database_url=pg_url,
        data_dir=tmp_path_factory.mktemp("agent") / "data",
        # Hash embeddings rank by chance, so the threshold tuned for the real model is off.
        refusal_min_retrieval_score=0.0,
    )


@pytest.fixture(scope="module")
def shop(pg_engine: Engine, agent_settings: Settings) -> Iterator[Built]:
    """``py_small`` (a tiny shop: a cart, helpers, an app, tests)."""
    empty_store(pg_engine)
    yield build_fixture("py_small", agent_settings, pg_engine)
    empty_store(pg_engine)
