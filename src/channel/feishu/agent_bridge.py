"""Bridge Feishu text messages into the LangChain agent."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence
from xml.sax.saxutils import escape

from .client import FeishuMessenger
from .messages import IncomingMessage


AgentRunner = Callable[[Sequence[Mapping[str, Any]], Sequence[Any]], str]


class FeishuAgentBridge:
    """Handle one Feishu text message by running the agent once."""

    def __init__(
        self,
        messenger: FeishuMessenger,
        *,
        model: Any | None = None,
        max_iterations: int = 1000,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self.messenger = messenger
        self.model = model
        self.max_iterations = max_iterations
        self.agent_runner = agent_runner

    def handle_message(self, message: IncomingMessage) -> None:
        tools = self._tools_for_message(message)
        messages = self._messages_for_message(message)
        try:
            output = self._run_agent(messages, tools)
        except Exception as exc:  # noqa: BLE001 - channel boundary returns a readable error.
            output = f"Agent execution failed: {exc}"
        self._send_response(message, output)

    def _tools_for_message(self, message: IncomingMessage) -> list[Any]:
        try:
            from src.tools.common import OperationNoticeTool, create_common_tools
        except ModuleNotFoundError:  # Supports running with src on PYTHONPATH.
            from tools.common import OperationNoticeTool, create_common_tools

        def notifier(text: str) -> None:
            self.messenger.send_text(message.chat_id, text)

        operation_notice = OperationNoticeTool(notifier=notifier)
        return create_common_tools(operation_notice_tool=operation_notice)

    def _messages_for_message(self, message: IncomingMessage) -> list[Mapping[str, Any]]:
        try:
            from src.prompts import build_initial_messages
        except ModuleNotFoundError:  # Supports running with src on PYTHONPATH.
            from prompts import build_initial_messages

        messages: list[Mapping[str, Any]] = []
        for content in build_initial_messages():
            messages.append({"role": "user", "content": content})
        messages.append(
            {
                "role": "user",
                "content": (
                    "<feishu_message>\n"
                    f"<chat_id>{_xml_text(message.chat_id)}</chat_id>\n"
                    f"<message_id>{_xml_text(message.message_id)}</message_id>\n"
                    f"<chat_type>{_xml_text(message.chat_type)}</chat_type>\n"
                    f"<sender_open_id>{_xml_text(message.sender_open_id)}</sender_open_id>\n"
                    f"<text>{_xml_text(message.text)}</text>\n"
                    "</feishu_message>"
                ),
            }
        )
        return messages

    def _run_agent(self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Any]) -> str:
        if self.agent_runner is not None:
            return self.agent_runner(messages, tools)

        try:
            from src.agent import build_agent
            from src.model import build_model
        except ModuleNotFoundError:  # Supports running with src on PYTHONPATH.
            from agent import build_agent
            from model import build_model

        model = self.model or build_model()
        agent = build_agent(model=model, tools=tools, name="feishu-agent")
        state = agent.invoke(
            {"messages": list(messages)},
            config={"recursion_limit": self.max_iterations},
        )
        return _latest_text(state)

    def _send_response(self, message: IncomingMessage, output: str) -> None:
        if message.chat_type == "p2p":
            self.messenger.send_text(message.chat_id, output)
        else:
            self.messenger.reply_text(message.message_id, output)


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


def _xml_text(value: Any) -> str:
    return escape(str(value or ""))
