"""Runtime context capture middleware for delegated subagents."""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from langchain.agents.middleware.types import AgentMiddleware


class RuntimeContextCaptureMiddleware(AgentMiddleware):
    """Capture the latest model-call messages for delegated subagent calls."""

    def __init__(self, set_messages: Callable[[Sequence[Any]], None]) -> None:
        self.set_messages = set_messages

    def before_model(self, state: Mapping[str, Any], runtime: Any | None = None) -> None:
        messages = state.get("messages") if isinstance(state, Mapping) else None
        if messages is not None:
            self.set_messages(copy.deepcopy(list(messages)))
        return None
