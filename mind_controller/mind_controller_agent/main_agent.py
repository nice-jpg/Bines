"""LangChain cognitive-control master."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import CognitiveSlave, SlaveDebugInfo
from .middleware import MasterTraceMiddleware
from .models import MasterRunResult
from .prompts import MASTER_SYSTEM_PROMPT, build_run_prompt
from .runtime import CognitiveController
from .tool_error import ToolErrorMiddleware

@dataclass(frozen=True)
class MasterConfig:
    max_rounds: int = 10
    target_score: int | None = None
    stale_rounds: int = 3
    model: str = "gpt-5.4"
    recursion_limit: int | None = None


def run_master(
    slave: CognitiveSlave,
    *,
    debug_info: SlaveDebugInfo | None = None,
    config: MasterConfig | None = None,
    model: Any | None = None,
) -> MasterRunResult:
    """Run one complete master invocation and leave the best prompts on disk."""

    selected = config or MasterConfig()
    info = _resolve_debug_info(slave, debug_info)
    controller = CognitiveController(
        slave=slave,
        debug_info=info,
        max_rounds=selected.max_rounds,
        target_score=selected.target_score,
        stale_rounds=selected.stale_rounds,
    )
    if model is None:
        chat_model = _build_default_model(selected.model)
    else:
        chat_model = model
    agent = build_master_agent(chat_model, controller)
    try:
        agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": build_run_prompt(
                            max_rounds=selected.max_rounds,
                            target_score=selected.target_score,
                            stale_rounds=selected.stale_rounds,
                        ),
                    }
                ]
            },
            config={
                "recursion_limit": selected.recursion_limit
                or max(60, selected.max_rounds * 18 + 30)
            },
        )
    finally:
        restored = controller.restore_best_prompts()
    if controller.best_score is None:
        raise RuntimeError("master finished without evaluating a slave run")
    return MasterRunResult(
        best_score=controller.best_score,
        best_round=controller.best_round,
        rounds=list(controller.rounds),
        changes=list(controller.changes),
        restored_best_prompts=restored,
    )


def build_master_agent(model: Any, controller: CognitiveController):
    from langchain.agents import create_agent

    return create_agent(
        model=model,
        tools=controller.build_tools(),
        system_prompt=MASTER_SYSTEM_PROMPT,
        name="cognitive_master",
        middleware=[
            MasterTraceMiddleware(controller),
            ToolErrorMiddleware()
        ],
    )


def _build_default_model(model_name: str) -> Any:
    """Load Bines' Codex model without importing the broader ``src`` package."""

    src_dir = Path(__file__).resolve().parents[2] / "src"
    src_entry = str(src_dir)
    if src_entry not in sys.path:
        sys.path.insert(0, src_entry)
    from model import build_codex_model

    return build_codex_model(model=model_name, reasoning_effort="high")


def _resolve_debug_info(
    slave: CognitiveSlave,
    supplied: SlaveDebugInfo | None,
) -> SlaveDebugInfo:
    if supplied is not None:
        return supplied
    provider = getattr(slave, "debug_info", None)
    if not callable(provider):
        raise ValueError(
            "provide debug_info=SlaveDebugInfo(...) or implement slave.debug_info()"
        )
    info = provider()
    if not isinstance(info, SlaveDebugInfo):
        raise TypeError("slave.debug_info() must return SlaveDebugInfo")
    return info
