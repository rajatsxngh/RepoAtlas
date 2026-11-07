"""Dependencies the routes share: settings, the engine, and the graph store of the app."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.store.graphstore import GraphStore


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def get_store(request: Request) -> GraphStore:
    return cast(GraphStore, request.app.state.store)


SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
StoreDep = Annotated[GraphStore, Depends(get_store)]
