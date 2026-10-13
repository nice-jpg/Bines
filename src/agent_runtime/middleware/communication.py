"""Conversation input and outgoing notifications at graph and tool boundaries."""

from collections.abc import Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from src.agent_runtime.conversation import ConversationSession, MessageBlock
from src.agent_runtime.messaging.messages import MessageStreamClosed, OutgoingMessage
from src.application.gateway_manager import GatewayManager
from src.message_types import read_input_messages
from src.tool_factory import create_tool
from src.tool_types import ToolResult

_tool_route: ContextVar[Mapping[str, str] | None] = ContextVar("agent_tool_route", default=None)


@contextmanager
def bind_message_route(route: Mapping[str, str]) -> Generator[None]:
    """Scope immutable address values to one tool call, never session references."""
    token: Token[Mapping[str, str] | None] = _tool_route.set(route)
    try:
        yield
    finally:
        _tool_route.reset(token)


def send_message(config: RunnableConfig, text: str) -> bool:
    bound: Mapping[str, str] | None = _tool_route.get()
    route: Mapping[str, object] = bound if bound is not None else config.get("configurable", {})
    gateway_id: object = route.get("gateway_id")
    channel_id: object = route.get("channel_id")
    conversation_id: object = route.get("conversation_id")
    if (
        not isinstance(gateway_id, str)
        or not isinstance(channel_id, str)
        or not isinstance(conversation_id, str)
    ):
        raise TypeError("Outgoing messages require a gateway and channel route")
    return GatewayManager.instance().send_message(
        gateway_id, OutgoingMessage(channel_id, conversation_id, text)
    )


def notify_current_call(text: str) -> None:
    if not send_message(get_config(), text):
        raise RuntimeError("Agent outgoing message queue rejected the notification")


def talk(message: str, runtime: ToolRuntime[None, AgentState]) -> bool:
    """Report a problem without pausing or waiting for a reply."""
    return send_message(runtime.config, message)


def create_talk_tool() -> StructuredTool:
    return create_tool(talk)


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
        self._tool_route: Mapping[str, str] = MappingProxyType(conversation.route)

    def before_model(self, state: AgentState, runtime: Runtime[None]) -> dict[str, list[HumanMessage]] | None:
        messages: list[HumanMessage] = read_pending_input(self._conversation, state)
        self._tool_route = MappingProxyType(self._conversation.route)
        if self._conversation.input_closed:
            interrupt({"kind": "shutdown", "message": "Runtime is shutting down."})
        return {"messages": messages} if messages else None

    async def abefore_model(
        self, state: AgentState, runtime: Runtime[None]
    ) -> dict[str, list[HumanMessage]] | None:
        return self.before_model(state, runtime)

    def after_model(self, state: AgentState, runtime: Runtime[None]) -> None:
        self._conversation.poll_input()
        self._tool_route = MappingProxyType(self._conversation.route)
        if self._conversation.input_closed:
            interrupt({"kind": "shutdown", "message": "Runtime is shutting down."})

    async def aafter_model(self, state: AgentState, runtime: Runtime[None]) -> None:
        self.after_model(state, runtime)

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        with bind_message_route(self._tool_route):
            return handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        with bind_message_route(self._tool_route):
            return await handler(request)
