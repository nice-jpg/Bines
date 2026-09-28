"""LangChain create_agent based agent harness.

This module intentionally does not implement the model/tool run loop itself.
LangChain's ``create_agent`` owns that loop through its graph runtime.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool

from langchain.agents.middleware.types import AgentMiddleware

try:
    from src.middleware import (
        CollectionPersistenceMiddleware,
        ConductorContextMiddleware,
        ProbeControlMiddleware,
        PROBE_COMPLETED,
        PROBE_STATUS,
        ToolExecutionPolicyMiddleware,
        create_captcha_human_in_the_loop_middleware,
        initial_page_context_state,
        initial_probe_state,
    )
    from src.page_mechanism import PageMechanism
    from src.tools import collect_conductor_tools
    from src.tools.event_hub import CONDUCTOR_EVENT_HUB_TOOL_NAMES, EventHub
    from src.tools.navigation import NAVIGATION_TOOL_NAMES
    from src.tools.operation_policy import (
        OperationNoticeService,
        operation_notice_service_from_metadata,
    )
    from src.prompts import COLLECTOR_PROMPT, CONDUCTOR_PROMPT
    from src.tasks import build_app_probe_messages, messages_request_app_probe, probe_task_spec
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from middleware import (
        CollectionPersistenceMiddleware,
        ConductorContextMiddleware,
        ProbeControlMiddleware,
        PROBE_COMPLETED,
        PROBE_STATUS,
        ToolExecutionPolicyMiddleware,
        create_captcha_human_in_the_loop_middleware,
        initial_page_context_state,
        initial_probe_state,
    )
    from page_mechanism import PageMechanism
    from tools import collect_conductor_tools
    from tools.event_hub import CONDUCTOR_EVENT_HUB_TOOL_NAMES, EventHub
    from tools.navigation import NAVIGATION_TOOL_NAMES
    from tools.operation_policy import OperationNoticeService, operation_notice_service_from_metadata
    from prompts import COLLECTOR_PROMPT, CONDUCTOR_PROMPT
    from tasks import build_app_probe_messages, messages_request_app_probe, probe_task_spec


from src.agent_runtime import AgentRunResult, AgentRuntime, create_runtime
from src.agent_runtime import AgentPersistenceLayer


class _ConductorRuntime(AgentRuntime):
    """Translate probe requests without coupling the generic runtime to devices."""

    def _prepare_messages(self, turn_messages, *, session_id):
        if messages_request_app_probe(turn_messages):
            return [*build_app_probe_messages(), *turn_messages]
        return turn_messages

    def _input_state(self, messages):
        state = {"messages": messages}
        if messages_request_app_probe(messages):
            state.update(initial_page_context_state())
            state.update(initial_probe_state(probe_task_spec().target_leaf_paths))
        return state

    def _completion_allowed(self, state):
        return state.get(PROBE_STATUS) in (None, "inactive", PROBE_COMPLETED)

    def _resume_payload(self, kind, user_input):
        message = "Human captcha authentication completed." if user_input is None else str(user_input)
        if kind == "liveness":
            return {"action": "continue", "message": message}
        return {"decisions": [{"type": "respond", "message": message}]}


def build_agent(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    system_prompt: str = CONDUCTOR_PROMPT,
    operation_notice_service: OperationNoticeService | None = None,
):
    """Create a standard LangChain agent with ``langchain.agents.create_agent``."""

    mechanism = PageMechanism()
    event_hub = EventHub()
    try:
        from src.collector import CollectorManager
    except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
        from collector import CollectorManager

    collector_manager = CollectorManager(
        model=model,
        mechanism=mechanism,
        event_hub=event_hub,
    )
    collection_persistence = CollectionPersistenceMiddleware(
        mechanism=mechanism,
        training_registry=collector_manager.training_registry,
        event_hub=event_hub,
    )
    probe_control = ProbeControlMiddleware(
        mechanism=mechanism,
        training_registry=collector_manager.training_registry,
    )
    registered_tools = _with_collected_tools(
        tools,
        [
            *collect_conductor_tools(
                model=model,
                mechanism=mechanism,
                event_hub=event_hub,
                collector_manager=collector_manager,
            ),
            *collection_persistence.create_tools(),
            *probe_control.create_tools(),
        ],
    )
    notice_service = operation_notice_service or next(
        (
            service
            for tool in registered_tools
            if (service := operation_notice_service_from_metadata(tool.metadata)) is not None
        ),
        None,
    )
    if notice_service is None:
        raise ValueError(
            "Conductor tools must include the typed internal operation notice service."
        )
    registered_tools = [
        tool
        for tool in registered_tools
        if operation_notice_service_from_metadata(tool.metadata) is None
    ]
    middlewares: list[AgentMiddleware] = [
        probe_control,
        collection_persistence,
        ToolExecutionPolicyMiddleware(
            event_hub=event_hub,
            notice_service=notice_service,
        ),
        ConductorContextMiddleware(mechanism=mechanism),
        create_captcha_human_in_the_loop_middleware(registered_tools),
    ]

    return _ConductorRuntime(name="conductor", model=model, tools=registered_tools,
                             system_prompt=system_prompt, middleware=middlewares)


def build_conductor_runtime(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any] = (),
    operation_notice_service: OperationNoticeService | None = None,
) -> Any:
    """Build the main conductor behind its checkpoint-preserving outer layer."""

    return AgentPersistenceLayer(
        build_agent(
            model=model,
            tools=tools,
            operation_notice_service=operation_notice_service,
        )
    )


def build_collector_runtime(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
) -> Any:
    """Build a collector agent with caller-provided page-scoped tools."""
    from src.collector.middleware import CollectorModelMiddleware
    return create_runtime(name="collector", model=model, tools=tools, system_prompt=COLLECTOR_PROMPT,
                          middleware=[CollectorModelMiddleware()])

def _with_collected_tools(tools: Sequence[Any], collected_tools: Sequence[Any]) -> list[Any]:

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
    "is_done",
    "update_page_notes",
    "authorize_probe_completion",
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


def _tool_name(tool: BaseTool | Mapping[str, Any]) -> str | None:
    if isinstance(tool, Mapping):
        value = tool.get("name")
        return str(value) if value else None
    value = tool.name
    return str(value) if value else None

def run_agent_loop(
    *,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    history: Iterable[BaseMessage | Mapping[str, Any]] | None = None,
    max_iterations: int = 8,
) -> AgentRunResult:
    """Run one user turn through LangChain's standard ``create_agent`` harness."""

    runtime = build_agent(
        model=model,
        tools=tools,
    )
    return runtime.run_turn(history or [], max_iterations=max_iterations)
