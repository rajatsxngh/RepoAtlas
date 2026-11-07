"""The OpenAPI spec (API-7), exported to ``backend/openapi.json``.

The web app's TypeScript types (``frontend/src/lib/api/schema.d.ts``) are generated from that
file with a pinned ``openapi-typescript`` (``scripts/gen_api_types.sh`` or ``.ps1``). Both files
are committed; a test and CI fail when either is out of date with the code.
"""

from __future__ import annotations

import json
from typing import Any

from repoatlas.api.app import create_app
from repoatlas.config import Settings

# The spec depends on the routes and models only; building it never opens a connection.
_PLACEHOLDER_DB = "postgresql+psycopg://openapi@localhost:5432/openapi"


def openapi_spec() -> dict[str, Any]:
    """The API's OpenAPI document."""
    app = create_app(Settings(_env_file=None, database_url=_PLACEHOLDER_DB))
    try:
        return app.openapi()
    finally:
        app.state.engine.dispose()


def render_spec(spec: dict[str, Any] | None = None) -> str:
    """The spec as committed: two-space JSON in the code's order, with a final newline."""
    return json.dumps(openapi_spec() if spec is None else spec, indent=2, ensure_ascii=False) + "\n"
