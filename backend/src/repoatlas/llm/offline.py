"""``OfflineTransport``: a stand-in that answers without calling a model (decision 0020).

It exists so a self-hosted stack or a CI job can check the ask path end to end with no API key
and no cost: retrieval, the prompt, the client's request rules and parsing, citation
validation, tracing, storage, and streaming all run as they do with the real transport. Only
the model is missing, so it proves the plumbing and says nothing about answer quality.

Given a request for an answer draft, it reads the evidence block headers the prompt layout
wrote (``<evidence id="E1" path="…" lines="10-24">``; repo text can't forge one, see
``repoatlas.answers.prompting.neutralize``) and returns a draft with one claim per evidence
block, for the first few. The answer text says plainly that no model wrote it, and it repeats
nothing from the repository. Usage is zero tokens, so every call costs $0.

Turn it on with ``REPOATLAS_LLM_TRANSPORT=offline``. ``LLMClient.from_settings`` refuses it
when an API key is set.
"""

from __future__ import annotations

import json
import re
from typing import Any

from repoatlas.llm.types import LLMRequestError

OFFLINE_MODEL = "offline-stand-in"
OFFLINE_NOTICE = (
    "**Offline stand-in: no model wrote this answer.** This server runs without a language "
    "model (`REPOATLAS_LLM_TRANSPORT=offline`), so the text below only shows which code was "
    "retrieved for the question."
)
MAX_OFFLINE_CLAIMS = 3
_EVIDENCE = re.compile(r'^<evidence id="(E\d+)" path="[^"\n]*" lines="(\d+)-(\d+)">$', re.MULTILINE)
_DRAFT_FIELDS = frozenset({"answer_markdown", "claims", "refused", "refusal_reason"})


class OfflineTransport:
    """Answers Messages API request bodies locally; never opens a connection."""

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        schema = (request.get("output_config") or {}).get("format", {}).get("schema")
        if schema is None:
            text = "Offline stand-in: no model was called."
        elif set(schema.get("properties", {})) == _DRAFT_FIELDS:
            text = json.dumps(_draft(_user_text(request)))
        else:
            raise LLMRequestError(
                "The offline stand-in only fills answer drafts; this request needs a model.",
                status=400,
            )
        return {
            "type": "message",
            "role": "assistant",
            "model": OFFLINE_MODEL,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


def _user_text(request: dict[str, Any]) -> str:
    """The text of the first user message (where the prompt layout puts the evidence)."""
    for message in request.get("messages", []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(
            block.get("text", "") for block in content or [] if block.get("type") == "text"
        )
    return ""


def _draft(prompt: str) -> dict[str, Any]:
    found = _EVIDENCE.findall(prompt)[:MAX_OFFLINE_CLAIMS]
    if not found:
        return {
            "answer_markdown": f"{OFFLINE_NOTICE}\n\nNo evidence was retrieved.",
            "claims": [],
            "refused": True,
            "refusal_reason": "The offline stand-in was given no evidence to cite.",
        }
    claims = [
        {
            "id": f"c{index}",
            "text": (
                f"Evidence {evidence_id} (lines {start} to {end} of the cited file) was "
                "retrieved for this question."
            ),
            "evidence_ids": [evidence_id],
            "symbol": None,
        }
        for index, (evidence_id, start, end) in enumerate(found, start=1)
    ]
    markers = "".join(f"[{claim['id']}]" for claim in claims)
    return {
        "answer_markdown": (
            f"{OFFLINE_NOTICE}\n\nThe best-matching code for the question is cited here. {markers}"
        ),
        "claims": claims,
        "refused": False,
        "refusal_reason": None,
    }
