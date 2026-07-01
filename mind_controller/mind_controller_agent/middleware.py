"""Trace middleware for the master agent."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from langchain_core.messages import SystemMessage


def _base_middleware():
    try:
        from langchain.agents.middleware.types import AgentMiddleware

        return AgentMiddleware
    except Exception:  # noqa: BLE001 - allow contract/runtime tests without LangChain.
        class AgentMiddlewareFallback:
            pass

        return AgentMiddlewareFallback


class MasterTraceMiddleware(_base_middleware()):
    """Append compact runtime state to each model request without persisting it."""

    def __init__(self, controller: Any) -> None:
        super().__init__()
        self.controller = controller

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """Keep the stable conversation prefix ahead of the dynamic trace."""

        return handler(
            request.override(messages=[*request.messages, self._trace_message()])
        )

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Async equivalent of ``wrap_model_call``."""

        return await handler(
            request.override(messages=[*request.messages, self._trace_message()])
        )

    def _trace_message(self) -> SystemMessage:
        rounds = [
            {
                "round": item.index,
                "total": item.score.total,
                "dimensions": item.score.dimensions,
                "prompt_revision": item.prompt_revision,
            }
            for item in self.controller.rounds
        ]
        changes = [asdict(item) for item in self.controller.changes[-6:]]
        return SystemMessage(
            content="Authoritative cognitive-control state: "
            + json.dumps(
                {
                    "rounds": rounds,
                    "changes": changes,
                    "best_score": self.controller.best_score,
                    "best_round": self.controller.best_round,
                },
                ensure_ascii=False,
            )
        )
