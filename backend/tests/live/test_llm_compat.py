"""Live compatibility checks (decision 0010 addendum): each case runs against the real API and
re-records its cassette in ``tests/llm_cassettes/compat``.

Opt in with ``uv run pytest -m live tests/live/test_llm_compat.py`` and
``REPOATLAS_ANTHROPIC_API_KEY`` in ``.env``. The whole file costs well under $0.05.
"""

from __future__ import annotations

import pytest

from repoatlas.config import get_settings
from repoatlas.llm.cassette import CassetteTransport
from repoatlas.llm.transport import LiteLLMTransport
from tests.llm_compat_cases import CASES, Case, run_case

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.mark.parametrize("case", [c for c in CASES if not c.docs_only], ids=lambda c: c.name)
async def test_compat_live(case: Case) -> None:
    key = get_settings().anthropic_api_key
    if key is None:
        pytest.skip("REPOATLAS_ANTHROPIC_API_KEY is not set.")
    recorder = CassetteTransport(
        case.path, mode="record", inner=LiteLLMTransport(key), description="; ".join(case.notes)
    )
    responses, error = await run_case(case, recorder)
    recorder.cassette.source = "live"
    case.check(responses, error)
    recorder.save()
