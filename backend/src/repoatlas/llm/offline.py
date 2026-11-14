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

It can also play an agent (decision 0022), so agent mode can be checked the same way. Given
a request with the agent's tools and no schema, it makes one ``search_code`` call with the
question, and once a tool result is in the conversation it calls ``final_answer``. Given the
final structured request, it fills the draft from the evidence lists of the tool results
(``<evidence id="E1" path="…" lines="10-24"/>``; tool output can't forge one either, see
``repoatlas.agent.guard``).

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
# The header may also carry flags="…" (kinds the injection detector marked in the block).
_EVIDENCE = re.compile(
    r'^<evidence id="(E\d+)" path="[^"\n]*" lines="(\d+)-(\d+)"(?: flags="[a-z_,]*")?>$',
    re.MULTILINE,
)
# An entry of a tool result's evidence list (agent mode).
_LISTED = re.compile(
    r'^<evidence id="(E\d+)" path="[^"\n]*" lines="(\d+)-(\d+)"(?: flags="[a-z_,]*")?/>$',
    re.MULTILINE,
)
_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)
_DRAFT_FIELDS = frozenset({"answer_markdown", "claims", "refused", "refusal_reason"})
_AGENT_TOOLS = frozenset({"search_code", "final_answer"})
MAX_OFFLINE_QUERY_CHARS = 300


class OfflineTransport:
    """Answers Messages API request bodies locally; never opens a connection."""

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        schema = (request.get("output_config") or {}).get("format", {}).get("schema")
        tools = {tool.get("name") for tool in request.get("tools") or []}
        if schema is None and tools >= _AGENT_TOOLS:
            return _message([_explore(request)], "tool_use")
        if schema is None:
            text = "Offline stand-in: no model was called."
        elif set(schema.get("properties", {})) == _DRAFT_FIELDS:
            results = _tool_results(request)
            found = _LISTED.findall(results) if tools else _EVIDENCE.findall(_user_text(request))
            text = json.dumps(_draft(found))
        else:
            raise LLMRequestError(
                "The offline stand-in only fills answer drafts; this request needs a model.",
                status=400,
            )
        return _message([{"type": "text", "text": text}], "end_turn")


def _message(content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "model": OFFLINE_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _tool_results(request: dict[str, Any]) -> str:
    """The text of every tool result in the conversation."""
    texts: list[str] = []
    for message in request.get("messages", []):
        content = message.get("content")
        if message.get("role") != "user" or isinstance(content, str):
            continue
        for block in content or []:
            if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                texts.append(block["content"])
    return "\n".join(texts)


def _explore(request: dict[str, Any]) -> dict[str, Any]:
    """The stand-in's next exploration step: search once with the question, then finish."""
    turns = sum(1 for message in request.get("messages", []) if message.get("role") == "assistant")
    if turns:
        return {
            "type": "tool_use",
            "id": f"toolu_offline_{turns + 1}",
            "name": "final_answer",
            "input": {},
        }
    asked = _QUESTION.search(_user_text(request))
    query = " ".join((asked[1] if asked else "").split())[:MAX_OFFLINE_QUERY_CHARS]
    return {
        "type": "tool_use",
        "id": "toolu_offline_1",
        "name": "search_code",
        "input": {"query": query or "main entry point", "k": 5},
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


def _draft(evidence: list[tuple[str, str, str]]) -> dict[str, Any]:
    """A draft with one claim per evidence entry (id, first line, last line), for the first
    few."""
    found = evidence[:MAX_OFFLINE_CLAIMS]
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
