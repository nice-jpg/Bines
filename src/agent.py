"""LangChain create_agent based agent harness.

This module intentionally does not implement the model/tool run loop itself.
LangChain's ``create_agent`` owns that loop through its graph runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
from typing import Any, Iterable, Mapping, Sequence

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

try:
    from src.middleware import (
        CollectionPersistenceMiddleware,
        CollectorReadinessMiddleware,
        DeviceContextCompressionMiddleware,
        PageContextMiddleware,
        PageManualMiddleware,
        ToolErrorMiddleware,
        create_captcha_human_in_the_loop_middleware,
        initial_page_context_state,
    )
    from src.page_mechanism import PageMechanism
    from src.tools import collect_conductor_tools
    from src.tools.content_provider import CollectionWorkbookSink
    from src.tools.event_hub import CONDUCTOR_EVENT_HUB_TOOL_NAMES, EventHub
    from src.tools.navigation import NAVIGATION_TOOL_NAMES
    from src.prompts import COLLECTOR_PROMPT, CONDUCTOR_PROMPT, SYSTEM_PROMPT
    from src.tasks import build_app_probe_messages, messages_request_app_probe
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from middleware import (
        CollectionPersistenceMiddleware,
        CollectorReadinessMiddleware,
        DeviceContextCompressionMiddleware,
        PageContextMiddleware,
        PageManualMiddleware,
        ToolErrorMiddleware,
        create_captcha_human_in_the_loop_middleware,
        initial_page_context_state,
    )
    from page_mechanism import PageMechanism
    from tools import collect_conductor_tools
    from tools.content_provider import CollectionWorkbookSink
    from tools.event_hub import CONDUCTOR_EVENT_HUB_TOOL_NAMES, EventHub
    from tools.navigation import NAVIGATION_TOOL_NAMES
    from prompts import COLLECTOR_PROMPT, CONDUCTOR_PROMPT, SYSTEM_PROMPT
    from tasks import build_app_probe_messages, messages_request_app_probe

@dataclass(frozen=True)
class AgentRunResult:
    """Normalized result returned by the LangChain agent harness."""

    output: str
    state: Mapping[str, Any]
    stopped_by: str
    interrupted: bool = False
    interrupts: list[Any] | None = None
    summary: list[Any] | None = None


class AgentRuntime:
    """Reusable agent runtime that separates initialization from interaction."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        tools: Sequence[Any] | None = None,
        name: str | None = None,
    ) -> None:
        self.model = model
        self.tools = list(tools or [])
        self.name = name
        self.agent = build_agent(model=model, tools=self.tools, name=name)
        self.sessions: dict[str, list[Any]] = {}
        self._stateless_counter = itertools.count(1)
        self._pending_thread_ids: dict[str, str] = {}

    def run_turn(
        self,
        messages: Iterable[BaseMessage | Mapping[str, Any]],
        *,
        session_id: str | None = None,
        tools: Sequence[Any] | None = None,
        max_iterations: int = 1000,
    ) -> AgentRunResult:
        """Run one interaction turn without rebuilding the model or agent."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if tools:
            raise ValueError("AgentRuntime tools are fixed at initialization to avoid rebuilding the agent.")

        turn_messages = list(messages)
        probe_requested = messages_request_app_probe(turn_messages)
        prepared_messages = self._prepare_messages(turn_messages, session_id=session_id)
        thread_id = self._thread_id_for_turn(session_id)
        input_state: dict[str, Any] = {"messages": prepared_messages}
        if probe_requested:
            input_state.update(initial_page_context_state())
        state = self.agent.invoke(
            input_state,
            config=_invoke_config(max_iterations, thread_id),
        )
        interrupts = _extract_interrupts(state)
        if session_id and interrupts:
            self._pending_thread_ids[session_id] = thread_id
        elif session_id:
            self._pending_thread_ids.pop(session_id, None)
        if session_id and not interrupts:
            self.sessions[session_id] = list(state.get("messages") or prepared_messages)
        return AgentRunResult(
            output=_latest_text(state),
            state=state,
            stopped_by="create_agent",
            interrupted=bool(interrupts),
            interrupts=interrupts,
        )

    def resume_turn(
        self,
        *,
        session_id: str,
        user_input: Any | None = None,
        max_iterations: int = 8,
    ) -> AgentRunResult:
        """Resume a previously interrupted LangGraph thread."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        thread_id = self._pending_thread_ids.get(session_id)
        if not thread_id:
            raise ValueError(f"No interrupted agent workflow is pending for session: {session_id}")

        resume_message = "Human captcha authentication completed." if user_input is None else str(user_input)
        state = self.agent.invoke(
            Command(resume={"decisions": [{"type": "respond", "message": resume_message}]}),
            config=_invoke_config(max_iterations, thread_id),
        )
        interrupts = _extract_interrupts(state)
        if interrupts:
            self._pending_thread_ids[session_id] = thread_id
        else:
            self._pending_thread_ids.pop(session_id, None)
            self.sessions[session_id] = list(state.get("messages") or self.sessions.get(session_id, []))
        return AgentRunResult(
            output=_latest_text(state),
            state=state,
            stopped_by="create_agent",
            interrupted=bool(interrupts),
            interrupts=interrupts,
        )

    def _prepare_messages(self, turn_messages: list[Any], *, session_id: str | None) -> list[Any]:
        messages: list[Any] = []
        if session_id:
            messages.extend(self.sessions.get(session_id, []))
        if messages_request_app_probe(turn_messages):
            messages.extend(build_app_probe_messages())
        messages.extend(turn_messages)
        return messages

    def has_pending_interrupt(self, session_id: str) -> bool:
        return session_id in self._pending_thread_ids

    def _thread_id_for_turn(self, session_id: str | None) -> str:
        if session_id:
            return session_id
        return f"stateless:{id(self)}:{next(self._stateless_counter)}"


def build_agent(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    name: str | None = None,
    system_prompt: str = CONDUCTOR_PROMPT,
):
    """Create a standard LangChain agent with ``langchain.agents.create_agent``."""

    mechanism = PageMechanism()
    event_hub = EventHub()
    try:
        from src.collector import CollectorManager
    except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
        from collector import CollectorManager

    collector_manager = CollectorManager(model=model, mechanism=mechanism, event_hub=event_hub)
    registered_tools = _with_collected_tools(
        tools,
        collect_conductor_tools(
            model=model,
            mechanism=mechanism,
            event_hub=event_hub,
            collector_manager=collector_manager,
        ),
    )
    workbook_sink = CollectionWorkbookSink()
    middlewares: list[AgentMiddleware] = []
    middlewares.append(ToolErrorMiddleware())
    middlewares.append(PageContextMiddleware(mechanism=mechanism))
    middlewares.append(
        CollectorReadinessMiddleware(
            mechanism=mechanism,
            training_registry=collector_manager.training_registry,
        )
    )
    middlewares.append(
        CollectionPersistenceMiddleware(
            mechanism=mechanism,
            training_registry=collector_manager.training_registry,
            workbook_sink=workbook_sink,
        )
    )
    middlewares.append(DeviceContextCompressionMiddleware())
    middlewares.append(create_captcha_human_in_the_loop_middleware(registered_tools))
    middlewares.append(SummarizationMiddleware(model=model))
    middlewares.append(PageManualMiddleware(mechanism=mechanism))

    return create_agent(
        model=model,
        tools=registered_tools,
        system_prompt=system_prompt,
        name=name,
        middleware=middlewares,
        checkpointer=InMemorySaver(),
    )


def build_conductor_runtime(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any] | None = None,
    name: str | None = "conductor",
) -> AgentRuntime:
    """Build the main page-operating conductor runtime."""

    return AgentRuntime(model=model, tools=list(tools or []), name=name)


def build_collector_runtime(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    name: str | None = "collector",
) -> Any:
    """Build a collector agent with caller-provided page-scoped tools."""

    return create_agent(
        model=model,
        tools=list(tools),
        system_prompt=COLLECTOR_PROMPT,
        name=name,
    )


def _invoke_config(max_iterations: int, thread_id: str) -> dict[str, Any]:
    return {
        "recursion_limit": max_iterations,
        "configurable": {"thread_id": thread_id},
    }


def _extract_interrupts(state: Mapping[str, Any]) -> list[Any]:
    interrupts = state.get("__interrupt__") or state.get("interrupts") or []
    if interrupts is None:
        return []
    if isinstance(interrupts, list):
        return interrupts
    if isinstance(interrupts, tuple):
        return list(interrupts)
    return [interrupts]


def _with_collected_tools(tools: Sequence[Any], collected_tools: Sequence[Any] | None = None) -> list[Any]:
    if collected_tools is None:
        raise ValueError("collected_tools is required for conductor runtime assembly.")

    registered_tools = list(tools)
    _ensure_unique_tool_names(registered_tools, source="caller-provided")
    _ensure_unique_tool_names(collected_tools, source="conductor")
    for tool in registered_tools:
        name = _tool_name(tool)
        if name in RUNTIME_MANAGED_TOOL_NAMES:
            raise ValueError(f"Tool {name!r} is managed by the conductor runtime and cannot be overridden.")
    existing_names = {_tool_name(tool) for tool in registered_tools}
    for tool in collected_tools:
        name = _tool_name(tool)
        if name in existing_names:
            if name in RUNTIME_MANAGED_TOOL_NAMES:
                raise ValueError(f"Tool {name!r} is managed by the conductor runtime and cannot be overridden.")
            continue
        registered_tools.append(tool)
        existing_names.add(name)
    return registered_tools


RUNTIME_MANAGED_TOOL_NAMES = {
    *CONDUCTOR_EVENT_HUB_TOOL_NAMES,
    *NAVIGATION_TOOL_NAMES,
    "tap",
    "swipe_back",
    "set_current_page",
    "query_page_doc",
    "query_task_doc",
    "prepare_collector",
}


def _ensure_unique_tool_names(tools: Sequence[Any], *, source: str) -> None:
    seen: set[str] = set()
    for tool in tools:
        name = _tool_name(tool)
        if not name:
            continue
        if name in seen:
            raise ValueError(f"Duplicate {source} tool name: {name!r}")
        seen.add(name)


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

    runtime = AgentRuntime(
        model=model,
        tools=tools,
        name=name,
    )
    return runtime.run_turn(history or [], max_iterations=max_iterations)

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
