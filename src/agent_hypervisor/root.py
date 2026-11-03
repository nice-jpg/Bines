"""Communication-only root engine."""

import asyncio
from collections.abc import Sequence
from threading import get_ident

from langchain_core.tools import BaseTool

from src.agent_runtime.contracts import AgentHandle
from src.agent_runtime.engine import AgentEngine
from src.channel.gateway import Gateway
from src.channel.messages import OutBoundMessage
from src.tools.shared.factory import create_tool


class RootAgent(AgentEngine):
    _can_reply = False

    def _create_fork(self, handle: AgentHandle) -> RootAgent:
        return RootAgent(model=self._model, handle=handle, max_iterations=self._max_iterations)

    def bind_gateway(self, gateway: Gateway) -> None:
        self._can_reply = True
        self._gateway = gateway
        self._gateway_loop = asyncio.get_running_loop()
        self._gateway_thread = get_ident()

    def _name(self) -> str:
        return "dialogue"

    def _system_prompt(self) -> str:
        return """You manage conversations across the configured communication channels.
External inputs have an empty task_id; reports use the short topic assigned by you.
Only you can reply externally with reply(task_id, message). Use an empty task_id for the current
external input; use the delegated topic for its progress or result, even after another user input arrives.
Reply before accept_report removes the task's routing record. Never copy technical routing IDs into tasks.
You have no device or collection tools. Delegate work with spawn_subagent and call.
Children are not bound to conversations. Reuse a child for later work from any conversation.
Choose distinct short topics for distinct active tasks. Preserve the user's intent when delegating;
send clarifications to the child handling that task.
Keep individual tasks separate. Continue handling other conversations while children work.
When nothing else needs action, end your turn to listen for reports; do not repeatedly call to poll.
Relay important progress and questions to the matching conversation.
When a user supplies clarification or captcha confirmation, forward it to the corresponding child.
Return to listening after sending replies or delegations; never perform business actions yourself.
"""

    def _create_tools(self) -> Sequence[BaseTool]:
        return [create_tool(self.reply)]

    def call(self, subagent_id: str, task_id: str, task: str) -> dict[str, str]:
        """Delegate a short task topic; its reply address is managed internally."""
        origin = self.conversation.current_origin
        if self._can_reply and not origin:
            raise ValueError("Receive user input before delegating channel work")
        topic = self.handle.mailbox.call(subagent_id, task_id, task, origin)
        return {"task_id": topic}

    def reply(self, task_id: str, message: str) -> bool:
        """Reply to a task's source; an empty topic targets the current external input."""
        if not self._can_reply:
            raise PermissionError("Only the channel root may send external replies")
        if get_ident() == self._gateway_thread:
            raise RuntimeError("Synchronous reply cannot block the channel loop")
        origin = self.handle.mailbox.task_origin(task_id) if task_id else self.conversation.current_origin
        channel_id, session_id = origin.get("channel_id"), origin.get("session_id")
        if not isinstance(channel_id, str) or not isinstance(session_id, str):
            raise ValueError("No external reply address is associated with this task")
        future = asyncio.run_coroutine_threadsafe(
            self._gateway.send(OutBoundMessage(channel_id, session_id, message)), self._gateway_loop
        )
        return future.result(timeout=35)
