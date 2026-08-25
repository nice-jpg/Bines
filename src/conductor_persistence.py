"""Persistent outer execution layer for the conductor runtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
import itertools
import json
from typing import Any, Iterable, Mapping, Sequence

from langchain_core.messages import BaseMessage

try:
    from src.agent import AgentRunResult, AgentRuntime
    from src.middleware.probe_state import (
        PROBE_COMPLETED,
        PROBE_INACTIVE,
        PROBE_STATUS,
    )
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from agent import AgentRunResult, AgentRuntime
    from middleware.probe_state import PROBE_COMPLETED, PROBE_INACTIVE, PROBE_STATUS


@dataclass(frozen=True)
class ConductorResponse:
    """Structured completion response emitted only when conductor work is resolved."""

    resolved: bool
    summary: str
    result: Mapping[str, Any]
    next_action: str | None

    @classmethod
    def parse(cls, content: str) -> ConductorResponse | None:
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        if set(payload) != {"resolved", "summary", "result", "next_action"}:
            return None
        resolved = payload.get("resolved")
        summary = payload.get("summary")
        result = payload.get("result")
        next_action = payload.get("next_action")
        if not isinstance(resolved, bool):
            return None
        if not isinstance(summary, str) or not summary.strip():
            return None
        if not isinstance(result, Mapping):
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "summary": self.summary,
            "result": dict(self.result),
            "next_action": self.next_action,
        }


class ConductorPersistenceLayer:
    """Keep invoking one conductor thread until completion or human input is required."""

    AUTO_RESUME_INTERRUPTS = frozenset({"liveness", "model_transport", "recursion"})

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        max_continuations: int | None = None,
    ) -> None:
        if max_continuations is not None and max_continuations < 0:
            raise ValueError("max_continuations must be >= 0 or None")
        self.runtime = runtime
        self.max_continuations = max_continuations
        self._session_counter = itertools.count(1)

    def run_turn(
        self,
        messages: Iterable[BaseMessage | Mapping[str, Any]],
        *,
        session_id: str | None = None,
        tools: Sequence[Any] | None = None,
        max_iterations: int = 1000,
    ) -> AgentRunResult:
        persistent_session_id = session_id or self._private_session_id()
        result = self.runtime.run_turn(
            messages,
            session_id=persistent_session_id,
            tools=tools,
            max_iterations=max_iterations,
        )
        return self._drive(
            result,
            session_id=persistent_session_id,
            max_iterations=max_iterations,
        )

    def resume_turn(
        self,
        *,
        session_id: str,
        user_input: Any | None = None,
        max_iterations: int = 1000,
    ) -> AgentRunResult:
        result = self.runtime.resume_turn(
            session_id=session_id,
            user_input=user_input,
            max_iterations=max_iterations,
        )
        return self._drive(result, session_id=session_id, max_iterations=max_iterations)

    def has_pending_interrupt(self, session_id: str) -> bool:
        return self.runtime.has_pending_interrupt(session_id)

    def pending_interrupt_kind(self, session_id: str) -> str | None:
        return self.runtime.pending_interrupt_kind(session_id)

    def _drive(
        self,
        result: AgentRunResult,
        *,
        session_id: str,
        max_iterations: int,
    ) -> AgentRunResult:
        continuations = 0
        while True:
            if result.interrupted:
                kind = self.runtime.pending_interrupt_kind(session_id)
                if kind not in self.AUTO_RESUME_INTERRUPTS:
                    return result
                if self._limit_reached(continuations):
                    return self._limit_result(result, continuations)
                continuations += 1
                result = self.runtime.resume_turn(
                    session_id=session_id,
                    user_input="continue" if kind == "liveness" else None,
                    max_iterations=max_iterations,
                )
                continue

            response = ConductorResponse.parse(result.output)
            accepted, reason = _completion_decision(result.state, response)
            if accepted and response is not None:
                payload = response.as_dict()
                return replace(
                    result,
                    output=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    stopped_by="completed",
                    structured_response=payload,
                )
            if self._limit_reached(continuations):
                return self._limit_result(result, continuations)
            continuations += 1
            result = self.runtime.continue_turn(
                session_id=session_id,
                reason=reason,
                max_iterations=max_iterations,
            )

    def _limit_reached(self, continuations: int) -> bool:
        return self.max_continuations is not None and continuations >= self.max_continuations

    def _limit_result(self, result: AgentRunResult, continuations: int) -> AgentRunResult:
        payload = {
            "resolved": False,
            "summary": "Conductor persistence continuation limit reached before task completion.",
            "result": {"continuations": continuations, "error_type": "persistence_limit"},
            "next_action": "Resume the same session.",
        }
        return replace(
            result,
            output=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            stopped_by="persistence_limit",
            structured_response=payload,
        )

    def _private_session_id(self) -> str:
        return f"persistent:{id(self)}:{next(self._session_counter)}"


def _completion_decision(
    state: Mapping[str, Any],
    response: ConductorResponse | None,
) -> tuple[bool, str]:
    status = str(state.get(PROBE_STATUS) or PROBE_INACTIVE)
    probe_complete = status in {PROBE_INACTIVE, PROBE_COMPLETED}
    if response is None:
        return False, "structured_response_required"
    if not response.resolved:
        return False, "conductor_reported_unresolved"
    if not probe_complete:
        return False, f"probe_state_{status}_is_not_complete"
    return True, ""


__all__ = ["ConductorPersistenceLayer", "ConductorResponse"]
