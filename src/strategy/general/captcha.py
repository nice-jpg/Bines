"""Human-in-the-loop middleware for captcha authentication."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime

from src.common.utils.json import JsonObject, json_object
from src.tools.shared.factory import invoke_tool_json

CAPTCHA_AUTHENTICATION_TOOL_NAME: Final[str] = "authenticate_captcha"
CAPTCHA_AUTHENTICATED_TOOL_NAME: Final[str] = "captcha_authenticated"
CAPTCHA_HITL_DESCRIPTION: Final[str] = (
    "Captcha authentication is required. Manually complete the captcha, "
    "then respond with the completion message so the agent can verify the UI again."
)


class CaptchaHumanInTheLoopMiddleware(HumanInTheLoopMiddleware):
    """Start captcha access before interrupting for human input."""

    def __init__(self, *, authentication_tool: BaseTool | None = None) -> None:
        super().__init__(
            interrupt_on={CAPTCHA_AUTHENTICATION_TOOL_NAME: _respond_interrupt_config()},
            description_prefix="Captcha authentication requires human input",
        )
        self.authentication_tool: BaseTool | None = authentication_tool
        self._started_tool_call_ids: set[str] = set()

    def after_model(self, state: AgentState, runtime: Runtime[None]) -> dict[str, object] | None:
        self._start_pending_authentication_sessions(state, runtime)
        return super().after_model(state, runtime)

    async def aafter_model(self, state: AgentState, runtime: Runtime[None]) -> dict[str, object] | None:
        return self.after_model(state, runtime)

    def _start_pending_authentication_sessions(self, state: AgentState, runtime: Runtime[None]) -> None:
        if self.authentication_tool is None:
            return
        messages: list[AnyMessage] = state.get("messages", [])
        if not messages:
            return

        last_ai_msg: AIMessage | None = next(
            (msg for msg in reversed(messages) if isinstance(msg, AIMessage) and msg.tool_calls),
            None,
        )
        if last_ai_msg is None:
            return

        for tool_call in last_ai_msg.tool_calls:
            if tool_call.get("name") != CAPTCHA_AUTHENTICATION_TOOL_NAME:
                continue
            config: InterruptOnConfig | None = self.interrupt_on.get(CAPTCHA_AUTHENTICATION_TOOL_NAME)
            if config is None or not self._should_interrupt(tool_call, config, state, runtime):
                continue
            call_id: str = str(tool_call.get("id") or "")
            if call_id and call_id in self._started_tool_call_ids:
                continue
            args: JsonObject = json_object(tool_call["args"])
            invoke_tool_json(self.authentication_tool, args)
            if call_id:
                self._started_tool_call_ids.add(call_id)


def create_captcha_human_in_the_loop_middleware(
    tools: Sequence[BaseTool] | None = None,
) -> CaptchaHumanInTheLoopMiddleware:
    """Create captcha HITL middleware and bind the captcha start tool."""

    authentication_tool: BaseTool | None = _find_tool(tools or (), CAPTCHA_AUTHENTICATION_TOOL_NAME)
    return CaptchaHumanInTheLoopMiddleware(authentication_tool=authentication_tool)


def _respond_interrupt_config() -> InterruptOnConfig:
    return InterruptOnConfig(
        allowed_decisions=["respond"],
        description=CAPTCHA_HITL_DESCRIPTION,
    )


def _find_tool(tools: Sequence[BaseTool], name: str) -> BaseTool | None:
    for tool in tools:
        tool_name: str = tool.name
        if tool_name == name:
            return tool
    return None
