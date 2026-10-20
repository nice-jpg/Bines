"""Synchronous subagent orchestration for the Bines LangChain agent."""

from __future__ import annotations

import copy
import itertools
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Final
from xml.sax.saxutils import escape

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool

from src.agent_messages.text import latest_message_text
from src.agent_messages.types import InputMessage, read_input_messages
from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.types.execution import EngineResult, EngineStatus
from src.common.utils.json import JsonValue
from src.common.utils.validation import is_string_mapping
from src.tools.communication.tools import create_talk_tool
from src.tools.shared.factory import create_tool

SUBAGENT_TOOL_NAMES: Final[set[str]] = {"spawn_subagent", "call_subagent", "kill_subagent"}
REQUIRED_SUBAGENT_TOOL_NAMES: Final[set[str]] = {
    "authenticate_captcha",
    "captcha_authenticated",
    "think",
}
FORK_BOILERPLATE_TAG: Final[str] = "fork_subagent_boilerplate"
SUBAGENT_RULES: Final[str] = """

Subagent rules:
- You are a subagent created by the main agent for a bounded task.
- Do not create, call, or delegate to any other subagent.
- Follow the instructions provided by the main agent and stay within that task boundary.
- Device operations must remain serial; finish your work and return a concise result before the main agent continues.
- If you discover a captcha, security check, slider verification, or human verification, call authenticate_captcha. Do not bypass or solve captcha screens yourself. When the user reports completion, call captcha_authenticated before reading or operating the device again.
- Final output must summarize what was solved, what was collected or written, and any blockers.
"""


@dataclass
class SubagentRecord:
    agent_id: str
    name: str
    agent_type: str
    instructions: str
    tools: Sequence[BaseTool]
    max_iterations: int
    messages: list[InputMessage]


class SubagentManager:
    """Manage synchronous child agents exposed as tools to the main agent."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        gateway_id: str,
        parent_context_provider: Callable[[], Sequence[InputMessage]] | None = None,
        tool_factory: Callable[[], Sequence[BaseTool]] | None = None,
        system_prompt: str = "",
    ) -> None:
        self._gateway_id: str = gateway_id
        self.model: str | BaseChatModel = model
        self.parent_context_provider: Callable[[], Sequence[InputMessage]] = (
            parent_context_provider or list[InputMessage]
        )
        self.tool_factory: Callable[[], Sequence[BaseTool]] = tool_factory or list[BaseTool]
        self.system_prompt: str = system_prompt
        self._records: dict[str, SubagentRecord] = {}
        self._counter: itertools.count[int] = itertools.count(1)
        self._lock: Lock = Lock()
        self._active_agent_id: str | None = None

    def spawn_subagent(
        self,
        name: str,
        agent_type: str,
        instructions: str,
        tool_names: str = "",
        max_iterations: int = 1000,
    ) -> str:
        """Create a subagent and return its registry id."""

        normalized_type: str = str(agent_type or "").strip().lower()
        if normalized_type not in {"independent", "delegated"}:
            return _error_result("invalid_agent_type", "agent_type must be independent or delegated.")

        normalized_instructions: str = str(instructions or "").strip()
        if not normalized_instructions:
            return _error_result("missing_instructions", "instructions must not be empty.")

        try:
            max_iterations = int(max_iterations)
        except (TypeError, ValueError):
            return _error_result("invalid_max_iterations", "max_iterations must be an integer.")
        if max_iterations < 1:
            return _error_result("invalid_max_iterations", "max_iterations must be >= 1.")

        available_tools: list[BaseTool] = list(self.tool_factory())
        selected_tools, error = self._select_tools(available_tools, tool_names)
        if error:
            return error

        agent_id: str = f"subagent-{next(self._counter)}"
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
            f'<subagent id="{_escape_attr(agent_id)}" '
            f'type="{_escape_attr(normalized_type)}" '
            f'name="{_escape_attr(self._records[agent_id].name)}">created</subagent>'
        )

    def call_subagent(self, agent_id: str, task: str) -> str:
        """Run a subagent synchronously and return its result."""

        normalized_id: str = str(agent_id or "").strip()
        task_text: str = str(task or "").strip()
        if not task_text:
            return _error_result("missing_task", "task must not be empty.")

        record: SubagentRecord | None = self._records.get(normalized_id)
        if record is None:
            return _error_result("subagent_not_found", f"No subagent found for id: {normalized_id}")

        if not self._lock.acquire(blocking=False):
            return _error_result("subagent_busy", "Another subagent call is already running.")

        self._active_agent_id = record.agent_id
        try:
            messages: list[InputMessage] = self._messages_for_call(record, task_text)
            agent: SubagentEngine = SubagentEngine(
                model=self.model,
                gateway_id=self._gateway_id,
                record=record,
                messages=messages,
                prompt=_system_prompt_for_subagent(self.system_prompt, record),
            )
            try:
                outcome: EngineResult = agent.run_turn()
                while outcome.status == EngineStatus.CONTINUE_REQUIRED:
                    outcome = agent.continue_turn()
                state: Mapping[str, object] = agent.history
            finally:
                agent.close()
            if outcome.status != EngineStatus.COMPLETED:
                return _error_result("subagent_interrupted", outcome.output)
            result_messages: list[InputMessage] = [*_state_messages(state)]
            if record.agent_type == "independent":
                record.messages = copy.deepcopy(result_messages)
            return _format_subagent_result(record, latest_message_text(state))
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

        normalized_id: str = str(agent_id or "").strip()
        if normalized_id not in self._records:
            return _error_result("subagent_not_found", f"No subagent found for id: {normalized_id}")
        del self._records[normalized_id]
        return f'<subagent id="{_escape_attr(normalized_id)}">killed</subagent>'

    @property
    def active_agent_id(self) -> str | None:
        return self._active_agent_id

    def _select_tools(
        self,
        available_tools: Sequence[BaseTool],
        tool_names: str,
    ) -> tuple[list[BaseTool], str | None]:
        tool_map: dict[str, BaseTool] = {tool.name: tool for tool in available_tools if tool.name}
        ordinary_tool_map: dict[str, BaseTool] = {
            name: tool for name, tool in tool_map.items() if name not in SUBAGENT_TOOL_NAMES
        }

        requested_names: list[str] = _parse_tool_names(tool_names)
        if not requested_names:
            selected_names: list[str] = list(ordinary_tool_map)
        else:
            selected_names = [name for name in requested_names if name not in SUBAGENT_TOOL_NAMES]

        selected_name_set: set[str] = set(selected_names)
        selected_name_set.update(name for name in REQUIRED_SUBAGENT_TOOL_NAMES if name in ordinary_tool_map)
        selected_names = [name for name in ordinary_tool_map if name in selected_name_set]

        unknown: list[str] = [name for name in selected_name_set if name not in ordinary_tool_map]
        if unknown:
            return [], _error_result(
                "unknown_tool",
                "Requested subagent tool is not available.",
                details={"tool_names": [*sorted(unknown)], "available_tools": [*sorted(ordinary_tool_map)]},
            )

        selected_tools: list[BaseTool] = [ordinary_tool_map[name] for name in selected_names]
        selected_tools.extend(_forbidden_subagent_tools())
        return selected_tools, None

    def _messages_for_call(self, record: SubagentRecord, task: str) -> list[InputMessage]:
        task_message: InputMessage = {
            "role": "user",
            "content": _build_child_message(record.instructions, task, fork=record.agent_type == "delegated"),
        }
        if record.agent_type == "delegated":
            return [*copy.deepcopy(list(self.parent_context_provider())), task_message]
        return [*copy.deepcopy(record.messages), task_message]


class SubagentEngine(AgentEngine):
    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        gateway_id: str,
        record: SubagentRecord,
        messages: Sequence[InputMessage],
        prompt: str,
    ) -> None:
        self._task_messages: tuple[InputMessage, ...] = tuple(messages)
        self._record: SubagentRecord = record
        self._prompt: str = prompt
        super().__init__(
            model=model,
            gateway_id=gateway_id,
            max_iterations=record.max_iterations,
        )

    def _create_initial_messages(self) -> Sequence[InputMessage]:
        return self._task_messages

    def _name(self) -> str:
        return self._record.name

    def _create_tools(self) -> Sequence[BaseTool]:
        return (
            list(self._record.tools)
            if any(tool.name == "talk" for tool in self._record.tools)
            else [*self._record.tools, create_talk_tool(self.send_message)]
        )

    def _system_prompt(self) -> str:
        return self._prompt

    @property
    def history(self) -> Mapping[str, object]:
        return dict(self._state)


def _parse_tool_names(tool_names: str) -> list[str]:
    raw: str = str(tool_names or "").strip()
    if not raw:
        return []
    return [part for part in re.split(r"[\s,]+", raw) if part]


def _system_prompt_for_subagent(system_prompt: str, record: SubagentRecord) -> str:
    if record.agent_type == "delegated":
        return system_prompt
    return f"{system_prompt}{SUBAGENT_RULES}\n\nMain-agent instructions:\n{record.instructions}"


def _build_child_message(instructions: str, task: str, *, fork: bool) -> str:
    if not fork:
        return (
            "<subagent_task>\n"
            f"<instructions>{_escape_text(instructions)}</instructions>\n"
            f"<task>{_escape_text(task)}</task>\n"
            "</subagent_task>"
        )

    return (
        f"<{FORK_BOILERPLATE_TAG}>\n"
        "STOP. READ THIS FIRST.\n\n"
        "You are a delegated subagent forked from the main agent. You are not the main agent.\n\n"
        "Rules:\n"
        "1. Do not create, spawn, call, or delegate to any other subagent.\n"
        "2. Follow the supplied page instructions before page-specific operations.\n"
        "3. Use page_view first; use screenshot only when XML is insufficient or misleading.\n"
        "4. Do not rely on merchant descriptions or page summaries from the main agent; inspect the current page yourself.\n"
        "5. Stay strictly within the assigned task and return one concise factual result.\n"
        "6. Device operations are serial. Finish and report before the main agent continues.\n\n"
        "<instructions>\n"
        f"{_escape_text(instructions)}\n"
        "</instructions>\n"
        "<task>\n"
        f"{_escape_text(task)}\n"
        "</task>\n"
        f"</{FORK_BOILERPLATE_TAG}>"
    )


def _forbidden_subagent_tools() -> list[StructuredTool]:
    return [
        create_tool(
            func=_forbidden_spawn_subagent,
            name="spawn_subagent",
            description="Forbidden inside subagents. Subagents cannot create nested subagents.",
        ),
        create_tool(
            func=_forbidden_call_subagent,
            name="call_subagent",
            description="Forbidden inside subagents. Subagents cannot call nested subagents.",
        ),
        create_tool(
            func=_forbidden_kill_subagent,
            name="kill_subagent",
            description="Forbidden inside subagents. Subagents cannot manage nested subagents.",
        ),
    ]


def _forbidden_spawn_subagent(
    name: str,
    agent_type: str,
    instructions: str,
    tool_names: str = "",
    max_iterations: int = 80,
) -> str:
    return _error_result("subagent_delegation_forbidden", "Subagents cannot spawn nested subagents.")


def _forbidden_call_subagent(agent_id: str, task: str) -> str:
    return _error_result("subagent_delegation_forbidden", "Subagents cannot call nested subagents.")


def _forbidden_kill_subagent(agent_id: str) -> str:
    return _error_result("subagent_delegation_forbidden", "Subagents cannot manage nested subagents.")


def _state_messages(state: object) -> Sequence[InputMessage]:
    if not is_string_mapping(state):
        raise TypeError("Subagent state must be a mapping")
    return read_input_messages(state.get("messages", []))


def _format_subagent_result(record: SubagentRecord, output: str) -> str:
    return (
        f'<subagent_result id="{_escape_attr(record.agent_id)}" '
        f'type="{_escape_attr(record.agent_type)}" '
        f'name="{_escape_attr(record.name)}">'
        f"\n<output>{_escape_text(output)}</output>\n"
        "</subagent_result>"
    )


def _error_result(code: str, message: str, details: Mapping[str, JsonValue] | None = None) -> str:
    lines: list[str] = [
        f'<subagent_error code="{_escape_attr(code)}">',
        f"<message>{_escape_text(message)}</message>",
    ]
    if details:
        lines.append("<details>")
        for key, value in details.items():
            lines.append(f"<{key}>{_escape_text(value)}</{key}>")
        lines.append("</details>")
    lines.append("</subagent_error>")
    return "\n".join(lines)


def _escape_text(value: JsonValue) -> str:
    return escape(str(value))


def _escape_attr(value: JsonValue) -> str:
    return escape(str(value), {'"': "&quot;"})
