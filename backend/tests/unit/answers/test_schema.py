"""Answer schemas: the flat schema the model fills, Pydantic limits, evidence numbering."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repoatlas.answers.schema import (
    MAX_CLAIMS,
    AnswerDraft,
    AnswerResult,
    Budget,
    Claim,
    answer_draft_schema,
    number_evidence,
)
from repoatlas.llm.compat import UNSUPPORTED_KEYWORDS, schema_keywords
from tests.unit.answers.helpers import EVIDENCE


def test_the_draft_schema_is_flat_and_closed() -> None:
    schema = answer_draft_schema()
    assert not schema_keywords(schema) & (UNSUPPORTED_KEYWORDS | {"$ref", "$defs"})
    assert schema["required"] == ["answer_markdown", "claims", "refused", "refusal_reason"]
    claim = schema["properties"]["claims"]["items"]
    assert claim["additionalProperties"] is False
    assert claim["required"] == ["id", "text", "evidence_ids", "symbol"]
    assert claim["properties"]["evidence_ids"] == {"type": "array", "items": {"type": "string"}}
    # Optional fields are nullable, not missing.
    assert {"type": "null"} in claim["properties"]["symbol"]["anyOf"]


def test_limits_are_enforced_by_pydantic() -> None:
    claim = {"id": "c1", "text": "x", "evidence_ids": ["E1"], "symbol": None}
    with pytest.raises(ValidationError):
        AnswerDraft.model_validate(
            {"answer_markdown": "a", "claims": [claim] * (MAX_CLAIMS + 1), "refused": False}
        )
    with pytest.raises(ValidationError):
        Claim.model_validate({**claim, "text": ""})
    with pytest.raises(ValidationError):
        Claim.model_validate({**claim, "evidence_ids": ["E1"] * 7})


def test_a_draft_round_trips() -> None:
    draft = AnswerDraft(
        answer_markdown="`create_app` builds the app [c1].",
        claims=[Claim(id="c1", text="create_app builds the app", evidence_ids=["E1"])],
        refused=False,
    )
    assert AnswerDraft.model_validate_json(draft.model_dump_json()) == draft


def test_evidence_is_numbered_in_order() -> None:
    numbered = number_evidence(EVIDENCE)
    assert list(numbered) == ["E1", "E2", "E3"]
    assert numbered["E1"].ref == EVIDENCE[0].ref
    assert numbered["E3"].item is EVIDENCE[2]
    assert number_evidence([]) == {}


def test_result_and_budget_defaults() -> None:
    result = AnswerResult(answer_markdown="", claims=[], citations=[], refused=True)
    assert result.refusal_kind is None
    assert result.metrics.prefilter_citation_validity is None
    assert Budget().max_cost_usd > 0
    with pytest.raises(ValidationError):
        Budget(max_cost_usd=0)
