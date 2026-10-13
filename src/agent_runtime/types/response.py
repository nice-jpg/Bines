"""Structured conductor completion response."""

import json
from dataclasses import dataclass

from src.json_types import JsonObject, JsonValue, load_json


@dataclass(frozen=True)
class AgentResponse:
    """Structured completion response emitted only when agent work is resolved."""

    resolved: bool
    summary: str
    result: JsonObject
    next_action: str | None

    @classmethod
    def parse(cls, content: str) -> AgentResponse | None:
        try:
            payload: JsonValue = load_json(content)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if set(payload) != {"resolved", "summary", "result", "next_action"}:
            return None
        resolved: JsonValue = payload.get("resolved")
        summary: JsonValue = payload.get("summary")
        result: JsonValue = payload.get("result")
        next_action: JsonValue = payload.get("next_action")
        if not isinstance(resolved, bool):
            return None
        if not isinstance(summary, str) or not summary.strip():
            return None
        if not isinstance(result, dict):
            return None
        if next_action is not None and not isinstance(next_action, str):
            return None
        if resolved and next_action is not None:
            return None
        if not resolved and (not isinstance(next_action, str) or not next_action.strip()):
            return None
        return cls(
            resolved=resolved,
            summary=summary.strip(),
            result=dict(result),
            next_action=next_action.strip() if isinstance(next_action, str) else None,
        )

    def as_dict(self) -> JsonObject:
        return {
            "resolved": self.resolved,
            "summary": self.summary,
            "result": dict(self.result),
            "next_action": self.next_action,
        }
