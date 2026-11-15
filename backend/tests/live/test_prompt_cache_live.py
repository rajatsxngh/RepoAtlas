"""Cache reads against the real API (PRF-5, decision 0024; ``docs/live-pass.md``, item 21).

Opt in: ``uv run pytest -m live tests/live/test_prompt_cache_live.py`` with
``REPOATLAS_ANTHROPIC_API_KEY`` in ``.env``. A handful of small-model calls with the agent's
own system prompt and tools: about a cent. It records nothing; it only checks that the
breakpoints ``repoatlas.llm.compat`` sets are honoured:

1. a call with tools, sent twice, reads its static prefix (tools and system prompt) from the
   cache the second time;
2. in a tool conversation, each step reads more from the cache than the step before it: the
   steps already sent are not paid for again.

If the API rejects a ``cache_control`` field, the first call fails with its 400: fix
``repoatlas.llm.compat`` (or set ``REPOATLAS_LLM_CACHE_CONVERSATION=false``) before any
evaluation run in agent or multi-agent mode.
"""

from __future__ import annotations

import pytest

from repoatlas.agent.prompting import FINAL_ANSWER_TOOL, build_agent_messages
from repoatlas.agent.tools import tool_definitions
from repoatlas.config import get_settings
from repoatlas.llm.client import LLMClient
from repoatlas.llm.compat import MIN_CACHEABLE_TOKENS
from repoatlas.llm.types import LLMResult, Message

pytestmark = [pytest.mark.live, pytest.mark.anyio]

QUESTION = "Where is the cart's total computed, and what calls it?"
# A tool result long enough to be worth caching by itself (well over the model's minimum).
LISTING = "\n".join(
    f"{line:4d} | def step_{line}(cart):  # line {line} of src/shop/cart.py"
    for line in range(1, 161)
)
RESULT = (
    f'Matches, best first:\n{LISTING}\n<evidence id="E1" path="src/shop/cart.py" lines="1-160"/>'
)


def client() -> LLMClient:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        pytest.skip("REPOATLAS_ANTHROPIC_API_KEY is not set.")
    return LLMClient.from_settings(settings)


def follow_up(result: LLMResult, text: str) -> list[Message]:
    """The model's turn, then the user's: a result for every tool call it made, or, if it
    made none, a plain request to go on."""
    if result.tool_calls:
        content = [
            {"type": "tool_result", "tool_use_id": call.id, "content": text}
            for call in result.tool_calls
        ]
    else:
        content = [{"type": "text", "text": f"{text}\n\nGo on: look up another symbol."}]
    return [result.assistant_message, {"role": "user", "content": content}]


async def test_the_static_prefix_and_the_growing_conversation_are_read_from_the_cache() -> None:
    llm = client()
    tools = [*tool_definitions(), FINAL_ANSWER_TOOL]
    messages = build_agent_messages(QUESTION, 12)

    first = await llm.complete("small", messages, tools=tools, max_tokens=1024)
    again = await llm.complete("small", messages, tools=tools, max_tokens=1024)
    print(f"\nfirst:  {first.usage}\nagain:  {again.usage}")
    written = first.usage.cache_write_tokens + first.usage.cache_read_tokens
    assert written >= MIN_CACHEABLE_TOKENS, "the static prefix was not cached at all"
    assert again.usage.cache_read_tokens >= written, "the second call did not read the prefix"

    # Two more steps of the same conversation.
    second_messages = [*messages, *follow_up(first, RESULT)]
    second = await llm.complete("small", second_messages, tools=tools, max_tokens=1024)
    third_messages = [*second_messages, *follow_up(second, RESULT.replace("step_", "part_"))]
    third = await llm.complete("small", third_messages, tools=tools, max_tokens=1024)
    print(f"second: {second.usage}\nthird:  {third.usage}")
    # Step two reads at least what step one cached (the prefix and the question).
    assert second.usage.cache_read_tokens >= written
    # Step three also reads step two's tool result: more than step two could read.
    assert third.usage.cache_read_tokens > second.usage.cache_read_tokens
    # And what it pays in full is only what is new since step two.
    assert third.usage.input_tokens < second.usage.total_input_tokens
