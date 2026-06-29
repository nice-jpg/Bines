"""Trace middleware for the master agent."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Mapping


def _base_middleware():
    try:
        from langchain.agents.middleware.types import AgentMiddleware

        return AgentMiddleware
    except Exception:  # noqa: BLE001 - allow contract/runtime tests without LangChain.
        class AgentMiddlewareFallback:
            pass

        return AgentMiddlewareFallback


class MasterTraceMiddleware(_base_middleware()):
    """Inject compact authoritative runtime state before each model step."""

    def __init__(self, controller: Any) -> None:
        super().__init__()
        self.controller = controller

    def before_model(
        self,
        state: Mapping[str, Any],
        runtime: Any | None = None,
    ) -> dict[str, Any] | None:
        messages = list(state.get("messages") or []) if isinstance(state, Mapping) else []
        if not messages:
            return None
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
        messages.insert(
            0,
            {
                "role": "system",
                "content": "Authoritative cognitive-control state: "
                + json.dumps(
                    {
                        "rounds": rounds,
                        "changes": changes,
                        "best_score": self.controller.best_score,
                        "best_round": self.controller.best_round,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        return {"messages": messages}

