"""Compatibility exports for split agent prompts."""

from .collector_coder_prompt import COLLECTOR_CODER_PROMPT
from .collector_prompt import COLLECTOR_PROMPT
from .conductor_prompt import CONDUCTOR_PROMPT, SYSTEM_PROMPT
from .patch_grammar import RUN_AGENT_PY_PATCH_GRAMMAR

__all__ = [
    "COLLECTOR_CODER_PROMPT",
    "COLLECTOR_PROMPT",
    "CONDUCTOR_PROMPT",
    "RUN_AGENT_PY_PATCH_GRAMMAR",
    "SYSTEM_PROMPT",
]
