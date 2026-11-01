"""Conversation input synchronization at model-call boundaries."""

from collections.abc import Mapping

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from src.agent_messages.types import read_input_messages
from src.agent_runtime.contracts import MessageStreamClosed
from src.agent_runtime.conversation import ConversationSession, MessageBlock


def read_pending_input(conversation: ConversationSession, state: Mapping[str, object]) -> list[HumanMessage]:
    seen: set[str] = set()
    for item in read_input_messages(state.get("messages", [])):
        identity: object = item.id if isinstance(item, BaseMessage) else item.get("id")
        if isinstance(identity, str):
            seen.add(identity)
    block: MessageBlock | None = conversation.pending_block
    if block is not None and block.block_id in seen:
        conversation.acknowledge(block.block_id)
    try:
        block = conversation.read_input(wait=False)
    except MessageStreamClosed:
        return []
    if block is None:
        return []
    return [HumanMessage(content=block.text, id=block.block_id)]


class ConversationInputMiddleware(AgentMiddleware[AgentState, None]):
    def __init__(self, conversation: ConversationSession) -> None:
        self._conversation: ConversationSession = conversation

    def before_model(self, state: AgentState, runtime: Runtime[None]) -> dict[str, list[HumanMessage]] | None:
        messages: list[HumanMessage] = read_pending_input(self._conversation, state)
        if self._conversation.input_closed:
            interrupt({"kind": "shutdown", "message": "Runtime is shutting down."})
        return {"messages": messages} if messages else None

    async def abefore_model(
        self, state: AgentState, runtime: Runtime[None]
    ) -> dict[str, list[HumanMessage]] | None:
        return self.before_model(state, runtime)

    def after_model(self, state: AgentState, runtime: Runtime[None]) -> None:
        self._conversation.poll_input()
        if self._conversation.input_closed:
            interrupt({"kind": "shutdown", "message": "Runtime is shutting down."})

    async def aafter_model(self, state: AgentState, runtime: Runtime[None]) -> None:
        self.after_model(state, runtime)
