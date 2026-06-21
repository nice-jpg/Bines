"""LangChain create_agent based agent harness.

This module intentionally does not implement the model/tool run loop itself.
LangChain's ``create_agent`` owns that loop through its graph runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig, TodoListMiddleware
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.types import AgentMiddleware

try:
    from src.middleware import DeviceContextCompressionMiddleware, RuntimeContextCaptureMiddleware
    from src.subagents_manager import SubagentManager
    from src.tools import collect_tools
    from src.prompts import SYSTEM_PROMPT, build_initial_messages
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from middleware import DeviceContextCompressionMiddleware, RuntimeContextCaptureMiddleware
    from subagents_manager import SubagentManager
    from tools import collect_tools
    from prompts import SYSTEM_PROMPT, build_initial_messages

@dataclass(frozen=True)
class AgentRunResult:
    """Normalized result returned by the LangChain agent harness."""

    output: str
    state: Mapping[str, Any]
    stopped_by: str


def build_agent(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    name: str | None = None,
):
    """Create a standard LangChain agent with ``langchain.agents.create_agent``."""

    runtime_messages: list[Any] = []

    def set_runtime_messages(messages: Sequence[Any]) -> None:
        runtime_messages.clear()
        runtime_messages.extend(messages)

    def parent_context_provider() -> list[Any]:
        return list(runtime_messages)

    subagent_manager = SubagentManager(
        model=model,
        parent_context_provider=parent_context_provider,
        tool_factory=lambda: collect_tools(include_subagents=False),
        system_prompt=SYSTEM_PROMPT,
    )
    middlewares: list[AgentMiddleware] = []
    # middlewares.append(TodoListMiddleware())
    middlewares.append(DeviceContextCompressionMiddleware())
    middlewares.append(RuntimeContextCaptureMiddleware(set_runtime_messages))
    middlewares.append(SummarizationMiddleware(model=model))
    registered_tools = _with_collected_tools(
        tools,
        collect_tools(include_subagents=True, subagent_manager=subagent_manager),
    )

    return create_agent(
        model=model,
        tools=registered_tools,
        system_prompt=SYSTEM_PROMPT,
        name=name,
        middleware=middlewares,
    )


def _with_collected_tools(tools: Sequence[Any], collected_tools: Sequence[Any] | None = None) -> list[Any]:
    registered_tools = list(tools)
    existing_names = {_tool_name(tool) for tool in registered_tools}
    for tool in collected_tools if collected_tools is not None else collect_tools():
        if _tool_name(tool) not in existing_names:
            registered_tools.append(tool)
    return registered_tools


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, Mapping):
        value = tool.get("name")
        return str(value) if value else None
    value = getattr(tool, "name", None)
    return str(value) if value else None

def run_agent_loop(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    history: Iterable[BaseMessage | Mapping[str, Any]] | None = None,
    max_iterations: int = 8,
    name: str | None = None,
) -> AgentRunResult:
    """Run one user turn through LangChain's standard ``create_agent`` harness."""

    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    agent = build_agent(model=model, 
                        tools=tools, 
                        name=name)
    messages = list(history or [])
    envs = build_initial_messages()
    for content in envs:
        messages.append({"role": "user", "content": content})
    state = agent.invoke(
        {"messages": messages},
        config={"recursion_limit": max_iterations},
    )

    return AgentRunResult(
        output=_latest_text(state),
        state=state,
        stopped_by="create_agent",
    )

def _latest_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""

    latest = messages[-1]
    content = getattr(latest, "content", None)
    if content is None and isinstance(latest, Mapping):
        content = latest.get("content")
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)
