"""Middleware and trace helpers for the optimizer agent."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .models import OptimizationRound, RecognizerResult


def _base_middleware():
    try:
        from langchain.agents.middleware.types import AgentMiddleware

        return AgentMiddleware
    except Exception:  # noqa: BLE001 - let unit tests import without LangChain.
        class AgentMiddlewareFallback:
            pass

        return AgentMiddlewareFallback


@dataclass
class OptimizerTrace:
    rounds: list[OptimizationRound] = field(default_factory=list)

    def append(self, round_result: OptimizationRound) -> None:
        self.rounds.append(round_result)

    def summary(self) -> str:
        if not self.rounds:
            return "No optimizer rounds have completed yet."
        compact = [
            {
                "round": item.index,
                "xml0_length": item.xml0_length,
                "xml1_length": item.xml1_length,
                "l0_count": item.l0_count,
                "l1_count": item.l1_count,
                "score": round(item.score.score, 4),
                "fidelity": round(item.score.fidelity, 4),
                "compression": round(item.score.compression, 4),
                "missing_count": item.score.missing_count,
            }
            for item in self.rounds
        ]
        return json.dumps(compact, ensure_ascii=False)


class OptimizerTraceMiddleware(_base_middleware()):
    """Inject compact optimizer trace into main-agent model calls."""

    def __init__(self, trace: OptimizerTrace) -> None:
        super().__init__()
        self.trace = trace

    def before_model(self, state: Mapping[str, Any], runtime: Any | None = None) -> dict[str, Any] | None:
        messages = list(state.get("messages") or []) if isinstance(state, Mapping) else []
        if not messages:
            return None
        messages.insert(
            0,
            {
                "role": "system",
                "content": "Optimizer round trace summary: " + self.trace.summary(),
            },
        )
        return {"messages": messages}


@dataclass(frozen=True)
class RecognizerCall:
    xml_length: int
    function_count: int
    ok: bool
    error: str | None


class RecognizerCommunicationMiddleware(_base_middleware()):
    """Own the main-agent to recognizer sub-agent communication path."""

    def __init__(self, model: Any) -> None:
        super().__init__()
        self.model = model
        self.calls: list[RecognizerCall] = []

    def call_recognizer(self, xml_text: str) -> RecognizerResult:
        from .recognizer_agent import recognize_functions

        result = recognize_functions(self.model, xml_text)
        self.calls.append(
            RecognizerCall(
                xml_length=len(xml_text),
                function_count=len(result.functions),
                ok=result.ok,
                error=result.error,
            )
        )
        return result

    def before_model(self, state: Mapping[str, Any], runtime: Any | None = None) -> dict[str, Any] | None:
        messages = list(state.get("messages") or []) if isinstance(state, Mapping) else []
        if not messages or not self.calls:
            return None
        latest = self.calls[-1]
        messages.insert(
            0,
            {
                "role": "system",
                "content": "Latest recognizer sub-agent call: "
                + json.dumps(asdict(latest), ensure_ascii=False),
            },
        )
        return {"messages": messages}


def round_to_json(round_result: OptimizationRound) -> str:
    return json.dumps(asdict(round_result), ensure_ascii=False, indent=2)
