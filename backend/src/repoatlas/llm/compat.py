"""Request and response rules for the current Claude models (decision 0010, addendum).

The compatibility spike's findings live here as code, so every call follows them:

- **Sampling parameters** (``temperature``, ``top_p``, ``top_k``) are never sent: non-default
  values return a 400 on Claude Sonnet 5.5 and Claude Haiku 5.5.
- **Tool choice** is ``auto`` or ``none`` only: forced ``any`` / ``tool`` return a 400 on
  Claude Sonnet 5.5, so a forced choice is refused here before any request is made.
- **Thinking** is adaptive and on by default on both models, so ``thinking`` is not sent.
  Thinking blocks come back with a signature and are passed back exactly as returned.
- **Structured outputs** use ``output_config.format`` (``json_schema``). Schemas are flattened:
  ``$ref`` inlined, no recursion, every object closed (``additionalProperties: false``) with all
  properties required, and keyword constraints the API does not accept (``minLength``,
  ``maxItems``, ``minimum``, …) removed. Pydantic enforces them after parsing instead.
- **Effort** goes in ``output_config.effort``.
- **Prompt caching** puts one ``cache_control`` breakpoint on the last system block (the
  static part). At most 4 breakpoints per request; prefixes under the model's minimum
  (512 tokens on these models) silently don't cache.
- **Refusals** arrive as ``stop_reason == "refusal"`` with ``stop_details``.
- **Conversations are append-only:** callers add turns; nothing here edits earlier ones.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Literal

from pydantic import BaseModel

from repoatlas.llm.types import Message, ToolCall, Usage

Effort = Literal["low", "medium", "high", "xhigh", "max"]
SAMPLING_PARAMS = frozenset({"temperature", "top_p", "top_k"})
ALLOWED_TOOL_CHOICES = frozenset({"auto", "none"})
MAX_CACHE_BREAKPOINTS = 4
MIN_CACHEABLE_TOKENS = 512
REFUSAL = "refusal"
THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})

# JSON Schema keywords the structured-output API rejects; Pydantic checks them after parsing.
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "default",
        "title",
        "examples",
    }
)


class SchemaError(ValueError):
    """The schema can't be sent as a structured output (for example, it is recursive)."""


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The flat JSON schema sent for ``model``: refs inlined, objects closed and fully
    required, unsupported keywords removed. Raises ``SchemaError`` for recursive models."""
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.pop("$defs", {})
    flat: dict[str, Any] = _flatten(schema, defs, ())
    return flat


def _flatten(node: Any, defs: dict[str, Any], seen: tuple[str, ...]) -> Any:
    if isinstance(node, list):
        return [_flatten(item, defs, seen) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        if name in seen:
            raise SchemaError(f"The schema is recursive through {name}; use a flat schema.")
        if name not in defs:
            raise SchemaError(f"The schema refers to an unknown definition {name}.")
        return _flatten(defs[name], defs, (*seen, name))
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYWORDS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _flatten(prop, defs, seen) for name, prop in value.items()}
        else:
            out[key] = _flatten(value, defs, seen)
    if out.get("type") == "object" or "properties" in out:
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def schema_keywords(schema: Any) -> set[str]:
    """Every key used anywhere in ``schema`` (for tests and checks)."""
    found: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            if key == "properties" and isinstance(value, dict):
                for prop in value.values():
                    found |= schema_keywords(prop)
            else:
                found |= schema_keywords(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= schema_keywords(item)
    return found


def build_request(
    model: str,
    messages: list[Message],
    *,
    max_tokens: int,
    schema: type[BaseModel] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    effort: Effort | None = None,
    cache_static: bool = True,
) -> dict[str, Any]:
    """The Messages API request body for one call.

    Leading ``system`` messages become the top-level ``system`` blocks; with
    ``cache_static`` the last of them carries the cache breakpoint. The rest of ``messages``
    is sent as given (deep-copied, never edited).
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive.")
    if tool_choice is not None and tool_choice not in ALLOWED_TOOL_CHOICES:
        raise ValueError(
            f"tool_choice {tool_choice!r} is not accepted by current Claude models; "
            "use 'auto' (and name the tool in the prompt) or 'none'."
        )
    system_blocks: list[dict[str, Any]] = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        system_blocks.extend(_text_blocks(messages[index]["content"]))
        index += 1
    conversation = copy.deepcopy(messages[index:])
    if not conversation:
        raise ValueError("A request needs at least one user message after the system prompt.")
    if conversation[0].get("role") != "user":
        raise ValueError("The first message after the system prompt must be from the user.")

    request: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": conversation}
    if system_blocks:
        if cache_static:
            system_blocks[-1] = {**system_blocks[-1], "cache_control": {"type": "ephemeral"}}
        request["system"] = system_blocks
    output_config: dict[str, Any] = {}
    if effort is not None:
        output_config["effort"] = effort
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema(schema)}
    if output_config:
        request["output_config"] = output_config
    if tools:
        request["tools"] = copy.deepcopy(tools)
        if tool_choice is not None:
            request["tool_choice"] = {"type": tool_choice}
    if count_cache_breakpoints(request) > MAX_CACHE_BREAKPOINTS:
        raise ValueError(f"A request may have at most {MAX_CACHE_BREAKPOINTS} cache breakpoints.")
    return request


def _text_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [copy.deepcopy(block) for block in content]


def count_cache_breakpoints(request: dict[str, Any]) -> int:
    """``cache_control`` markers in the request's tools, system, and messages."""
    count = 0
    for tool in request.get("tools", []):
        count += "cache_control" in tool
    for block in request.get("system", []):
        count += "cache_control" in block
    for message in request.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            count += sum("cache_control" in block for block in content if isinstance(block, dict))
    return count


class ParsedResponse(BaseModel):
    text: str
    tool_calls: list[ToolCall]
    thinking_blocks: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    usage: Usage
    stop_reason: str | None
    refused: bool
    refusal_category: str | None
    model: str | None


def parse_response(response: dict[str, Any]) -> ParsedResponse:
    """Read a Messages API response. Blocks are kept exactly as returned."""
    blocks: list[dict[str, Any]] = list(response.get("content") or [])
    text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    tool_calls = [
        ToolCall(id=block["id"], name=block["name"], input=_tool_input(block.get("input")))
        for block in blocks
        if block.get("type") == "tool_use"
    ]
    thinking = [block for block in blocks if block.get("type") in THINKING_TYPES]
    raw_usage = response.get("usage") or {}
    usage = Usage(
        input_tokens=int(raw_usage.get("input_tokens") or 0),
        output_tokens=int(raw_usage.get("output_tokens") or 0),
        cache_read_tokens=int(raw_usage.get("cache_read_input_tokens") or 0),
        cache_write_tokens=int(raw_usage.get("cache_creation_input_tokens") or 0),
    )
    stop_reason = response.get("stop_reason")
    details = response.get("stop_details") or {}
    refused = stop_reason == REFUSAL
    return ParsedResponse(
        text=text,
        tool_calls=tool_calls,
        thinking_blocks=thinking,
        blocks=blocks,
        usage=usage,
        stop_reason=stop_reason,
        refused=refused,
        refusal_category=details.get("category") if refused else None,
        model=response.get("model"),
    )


def _tool_input(value: Any) -> dict[str, Any]:
    """Tool inputs are parsed as JSON, never string-matched (escaping differs by model)."""
    if isinstance(value, str):
        parsed = json.loads(value) if value else {}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return dict(value or {})
