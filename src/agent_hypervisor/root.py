"""Communication-only root engine."""

import asyncio
from collections.abc import Sequence
from threading import get_ident

from langchain_core.tools import BaseTool

from src.agent_runtime.contracts import AgentHandle
from src.agent_runtime.engine import AgentEngine
from src.channel.gateway import Gateway
from src.channel.messages import OutBoundMessage
from src.common.utils.json import JsonObject
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
All conversations share this model context, but their message lists and replies stay separate.
Read session_id on user messages and task_id on reports; process events in sequence order.
Only you can reply to a channel using reply(channel_id, session_id, message). Never infer a reply address
from the last message when handling a result for another conversation.
You have no device or collection tools. Delegate work with spawn_subagent and call.
Children are not bound to conversations. Reuse a child for later work from any conversation.
Keep the task_id returned by each call associated with its originating conversation in your history.
Pass source_batch_ids when calling a child. Reports carry verified origin.sources;
use those channel_id/session_id addresses rather than the most recent conversation.
Preserve the original task text,
especially exact command phrases; send extra guidance as a subsequent call if necessary.
Keep individual tasks separate. Continue handling other conversations while children work.
When nothing else needs action, end your turn to listen for reports; do not repeatedly call to poll.
Relay important progress and questions to the matching conversation.
When a user supplies clarification or captcha confirmation, forward it to the corresponding child.
Return to listening after sending replies or delegations; never perform business actions yourself.
"""

    def _create_tools(self) -> Sequence[BaseTool]:
        return [create_tool(self.reply)]

    def call(self, subagent_id: str, task: str, source_batch_ids: list[str]) -> dict[str, str]:
        """Delegate with explicit source batch IDs; reports retain these reply addresses."""
        sources = {
            message.batch_id: message
            for messages in self.conversation.conversations.values()
            for message in messages
            if message.batch_id is not None
        }
        if self._can_reply and not source_batch_ids:
            raise ValueError("Root delegation requires source_batch_ids from received input")
        if any(identity not in sources for identity in source_batch_ids):
            raise ValueError("Unknown source batch")
        origin: JsonObject = {
            "sources": [
                {
                    "batch_id": identity,
                    "channel_id": sources[identity].channel_id,
                    "session_id": sources[identity].session_id,
                }
                for identity in dict.fromkeys(source_batch_ids)
            ]
        }
        task_id = self.handle.mailbox.call(subagent_id, task, origin)
        return {"task_id": task_id, "subagent_id": subagent_id}

    def reply(self, channel_id: str, session_id: str, message: str) -> bool:
        """Reply to a received conversation and return the channel's delivery status."""
        if not self._can_reply:
            raise PermissionError("Only the channel root may send external replies")
        if get_ident() == self._gateway_thread:
            raise RuntimeError("Synchronous reply cannot block the channel loop")
        future = asyncio.run_coroutine_threadsafe(
            self._gateway.send(OutBoundMessage(channel_id, session_id, message)), self._gateway_loop
        )
        return future.result(timeout=35)
