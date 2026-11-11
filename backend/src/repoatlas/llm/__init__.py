"""LLM access: one client over LiteLLM, request rules for current Claude models, and test tools
(decision 0010).

LiteLLM is configured here, before anything can import it: in its default ``DEV`` mode it loads
the nearest ``.env`` into ``os.environ`` at import (which would put our secrets in the process
environment), and it fetches its model price map and Anthropic beta-header config from GitHub.
Production mode and the bundled copies turn all three off; prices come from our settings.
"""

import os

for _name, _value in (
    ("LITELLM_MODE", "PRODUCTION"),
    ("LITELLM_LOCAL_MODEL_COST_MAP", "True"),
    ("LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS", "True"),
):
    os.environ.setdefault(_name, _value)
