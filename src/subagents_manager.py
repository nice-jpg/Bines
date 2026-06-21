"""Synchronous subagent orchestration for the Bines LangChain agent."""

from __future__ import annotations

import copy
import itertools
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Mapping, Sequence


SUBAGENT_TOOL_NAMES = {"spawn_subagent", "call_subagent", "kill_subagent"}
SUBAGENT_RULES = """

Subagent rules:
- You are a subagent created by the main agent for a bounded task.
- Do not create, call, or delegate to any other subagent.
- Follow the instructions provided by the main agent and stay within that task boundary.
- Device operations must remain serial; finish your work and return a concise result before the main agent continues.
- Final output must summarize what was solved, what was collected or written, and any blockers.
"""


@dataclass
class SubagentRecord:
    agent_id: str
    name: str
    agent_type: str
    instructions: str
    tools: Sequence[Any]
    max_iterations: int
    messages: list[Any]


class SubagentManager:
    """Manage synchronous child agents exposed as tools to the main agent."""

    def __init__(
        self,
        *,
        model: Any,
        parent_context_provider: Callable[[], Sequence[Any]] | None = None,
        tool_factory: Callable[[], Sequence[Any]] | None = None,
        system_prompt: str = "",
        create_agent_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model = model
        self.parent_context_provider = parent_context_provider or (lambda: [])
        self.tool_factory = tool_factory or (lambda: [])
        self.system_prompt = system_prompt
        self.create_agent_factory = create_agent_factory
        self._records: dict[str, SubagentRecord] = {}
        self._counter = itertools.count(1)
        self._lock = Lock()
        self._active_agent_id: str | None = None

    def spawn_subagent(
        self,
        name: str,
        agent_type: str,
        instructions: str,
        tool_names: str = "",
        max_iterations: int = 80,
    ) -> str:
        """Create a subagent and return its registry id."""

        normalized_type = str(agent_type or "").strip().lower()
        if normalized_type not in {"independent", "delegated"}:
            return _error_result("invalid_agent_type", "agent_type must be independent or delegated.")

        normalized_instructions = str(instructions or "").strip()
        if not normalized_instructions:
            return _error_result("missing_instructions", "instructions must not be empty.")

        try:
            max_iterations = int(max_iterations)
        except (TypeError, ValueError):
            return _error_result("invalid_max_iterations", "max_iterations must be an integer.")
        if max_iterations < 1:
            return _error_result("invalid_max_iterations", "max_iterations must be >= 1.")

        available_tools = list(self.tool_factory())
        selected_tools, error = self._select_tools(available_tools, tool_names)
        if error:
            return error

        agent_id = f"subagent-{next(self._counter)}"
        self._records[agent_id] = SubagentRecord(
            agent_id=agent_id,
            name=str(name or agent_id).strip() or agent_id,
            agent_type=normalized_type,
            instructions=normalized_instructions,
            tools=selected_tools,
            max_iterations=max_iterations,
            messages=[],
        )

        return (
            f"<subagent id=\"{_escape_attr(agent_id)}\" "
            f"type=\"{_escape_attr(normalized_type)}\" "
            f"name=\"{_escape_attr(self._records[agent_id].name)}\">created</subagent>"
        )

    def call_subagent(self, agent_id: str, task: str) -> str:
        """Run a subagent synchronously and return its result."""

        normalized_id = str(agent_id or "").strip()
        task_text = str(task or "").strip()
        if not task_text:
            return _error_result("missing_task", "task must not be empty.")

        record = self._records.get(normalized_id)
        if record is None:
            return _error_result("subagent_not_found", f"No subagent found for id: {normalized_id}")

        if not self._lock.acquire(blocking=False):
            return _error_result("subagent_busy", "Another subagent call is already running.")

        self._active_agent_id = record.agent_id
        try:
            messages = self._messages_for_call(record, task_text)
            agent = self._build_agent(record)
            state = agent.invoke(
                {"messages": messages},
                config={"recursion_limit": record.max_iterations},
            )
            result_messages = list(_state_messages(state))
            if record.agent_type == "independent":
                record.messages = copy.deepcopy(result_messages)
            return _format_subagent_result(record, _latest_text(state))
        except Exception as exc:  # noqa: BLE001 - tools return structured errors.
            return _error_result(
                "subagent_call_failed",
                str(exc),
                details={"agent_id": record.agent_id, "agent_type": record.agent_type},
            )
        finally:
            self._active_agent_id = None
            self._lock.release()

    def kill_subagent(self, agent_id: str) -> str:
        """Remove a subagent from the registry."""

        normalized_id = str(agent_id or "").strip()
        if normalized_id not in self._records:
            return _error_result("subagent_not_found", f"No subagent found for id: {normalized_id}")
        del self._records[normalized_id]
        return f"<subagent id=\"{_escape_attr(normalized_id)}\">killed</subagent>"

    @property
    def active_agent_id(self) -> str | None:
        return self._active_agent_id

    def _select_tools(self, available_tools: Sequence[Any], tool_names: str) -> tuple[list[Any], str | None]:
        tool_map = {_tool_name(tool): tool for tool in available_tools if _tool_name(tool)}
        forbidden = SUBAGENT_TOOL_NAMES & set(tool_map)
        if forbidden:
            tool_map = {name: tool for name, tool in tool_map.items() if name not in forbidden}

        requested_names = _parse_tool_names(tool_names)
        if not requested_names:
            return list(tool_map.values()), None

        forbidden_requested = SUBAGENT_TOOL_NAMES & set(requested_names)
        if forbidden_requested:
            return [], _error_result(
                "forbidden_tool",
                "Subagents cannot receive subagent delegation tools.",
                details={"tool_names": sorted(forbidden_requested)},
            )

        unknown = [name for name in requested_names if name not in tool_map]
        if unknown:
            return [], _error_result(
                "unknown_tool",
                "Requested subagent tool is not available.",
                details={"tool_names": unknown, "available_tools": sorted(tool_map)},
            )

        return [tool_map[name] for name in requested_names], None

    def _messages_for_call(self, record: SubagentRecord, task: str) -> list[Any]:
        task_message = {
            "role": "user",
            "content": (
                "<subagent_task>\n"
                f"<instructions>{_escape_text(record.instructions)}</instructions>\n"
                f"<task>{_escape_text(task)}</task>\n"
                "</subagent_task>"
            ),
        }
        if record.agent_type == "delegated":
            return [*copy.deepcopy(list(self.parent_context_provider())), task_message]
        return [*copy.deepcopy(record.messages), task_message]

    def _build_agent(self, record: SubagentRecord) -> Any:
        create_agent = self.create_agent_factory
        if create_agent is None:
            from langchain.agents import create_agent as create_agent  # noqa: PLC0415

        return create_agent(
            model=self.model,
            tools=list(record.tools),
            system_prompt=f"{self.system_prompt}{SUBAGENT_RULES}\n\nMain-agent instructions:\n{record.instructions}",
            name=record.name,
        )


def _parse_tool_names(tool_names: str) -> list[str]:
    raw = str(tool_names or "").strip()
    if not raw:
        return []
    return [part for part in re.split(r"[\s,]+", raw) if part]


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, Mapping):
        value = tool.get("name")
        return str(value) if value else None
    value = getattr(tool, "name", None)
    return str(value) if value else None


def _state_messages(state: Mapping[str, Any] | Any) -> Sequence[Any]:
    if isinstance(state, Mapping):
        return state.get("messages") or []
    return getattr(state, "messages", []) or []


def _latest_text(state: Mapping[str, Any] | Any) -> str:
    messages = _state_messages(state)
    if not messages:
        return ""
    latest = messages[-1]
    content = latest.get("content") if isinstance(latest, Mapping) else getattr(latest, "content", None)
    return content if isinstance(content, str) else str(content or "")


def _format_subagent_result(record: SubagentRecord, output: str) -> str:
    return (
        f"<subagent_result id=\"{_escape_attr(record.agent_id)}\" "
        f"type=\"{_escape_attr(record.agent_type)}\" "
        f"name=\"{_escape_attr(record.name)}\">"
        f"\n<output>{_escape_text(output)}</output>\n"
        "</subagent_result>"
    )


def _error_result(code: str, message: str, details: Mapping[str, Any] | None = None) -> str:
    lines = [f"<subagent_error code=\"{_escape_attr(code)}\">", f"<message>{_escape_text(message)}</message>"]
    if details:
        lines.append("<details>")
        for key, value in details.items():
            lines.append(f"<{key}>{_escape_text(value)}</{key}>")
        lines.append("</details>")
    lines.append("</subagent_error>")
    return "\n".join(lines)


def _escape_text(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_attr(value: Any) -> str:
    return _escape_text(value).replace('"', "&quot;")
