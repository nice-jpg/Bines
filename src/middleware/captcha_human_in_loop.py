"""Human-in-the-loop middleware for captcha authentication."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

try:
    from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
except (ImportError, ModuleNotFoundError):  # Lightweight fallback for tests that stub only AgentMiddleware.
    class HumanInTheLoopMiddleware(AgentMiddleware):  # type: ignore[no-redef]
        def __init__(self, interrupt_on=None, description_prefix: str = "") -> None:
            self.interrupt_on = interrupt_on or {}
            self.description_prefix = description_prefix

    class InterruptOnConfig(dict):  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)

CAPTCHA_AUTHENTICATION_TOOL_NAME = "authenticate_captcha"
CAPTCHA_AUTHENTICATED_TOOL_NAME = "captcha_authenticated"
CAPTCHA_HITL_DESCRIPTION = (
    "Captcha authentication is required. Manually complete the captcha, "
    "then respond with the completion message so the agent can verify the UI again."
)


class CaptchaHumanInTheLoopMiddleware(HumanInTheLoopMiddleware):
    """Start captcha access before interrupting for human input."""

    def __init__(self, *, authentication_tool: Any | None = None) -> None:
        super().__init__(
            interrupt_on={CAPTCHA_AUTHENTICATION_TOOL_NAME: _respond_interrupt_config()},
            description_prefix="Captcha authentication requires human input",
        )
        self.authentication_tool = authentication_tool
        self._started_tool_call_ids: set[str] = set()

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self._start_pending_authentication_sessions(state, runtime)
        return super().after_model(state, runtime)

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    def _start_pending_authentication_sessions(self, state: Any, runtime: Any) -> None:
        if self.authentication_tool is None:
            return
        messages = state["messages"] if isinstance(state, dict) else getattr(state, "messages", [])
        if not messages:
            return

        last_ai_msg = next((msg for msg in reversed(messages) if getattr(msg, "tool_calls", None)), None)
        if not last_ai_msg or not getattr(last_ai_msg, "tool_calls", None):
            return

        for tool_call in last_ai_msg.tool_calls:
            if tool_call.get("name") != CAPTCHA_AUTHENTICATION_TOOL_NAME:
                continue
            config = self.interrupt_on.get(CAPTCHA_AUTHENTICATION_TOOL_NAME)
            if config is None or not self._should_interrupt(tool_call, config, state, runtime):
                continue
            call_id = str(tool_call.get("id") or "")
            if call_id and call_id in self._started_tool_call_ids:
                continue
            if call_id:
                self._started_tool_call_ids.add(call_id)
            _invoke_tool(self.authentication_tool, tool_call.get("args") or {})


def create_captcha_human_in_the_loop_middleware(tools: Sequence[Any] | None = None) -> AgentMiddleware:
    """Create captcha HITL middleware and bind the captcha start tool."""

    authentication_tool = _find_tool(tools or (), CAPTCHA_AUTHENTICATION_TOOL_NAME)
    try:
        return CaptchaHumanInTheLoopMiddleware(authentication_tool=authentication_tool)
    except TypeError:
        # Lightweight unit-test stubs may not accept constructor args.
        middleware = HumanInTheLoopMiddleware()
        setattr(middleware, "interrupt_on", {CAPTCHA_AUTHENTICATION_TOOL_NAME: _respond_interrupt_config()})
        setattr(middleware, "authentication_tool", authentication_tool)
        return middleware


def _respond_interrupt_config() -> Any:
    try:
        return InterruptOnConfig(
            allowed_decisions=["respond"],
            description=CAPTCHA_HITL_DESCRIPTION,
        )
    except TypeError:
        return {
            "allowed_decisions": ["respond"],
            "description": CAPTCHA_HITL_DESCRIPTION,
        }


def _find_tool(tools: Sequence[Any], name: str) -> Any | None:
    for tool in tools:
        tool_name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
        if tool_name == name:
            return tool
    return None


def _invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    if hasattr(tool, "invoke"):
        return tool.invoke(args)
    func = getattr(tool, "func", None)
    if callable(func):
        return func(**args)
    if callable(tool):
        return tool(**args)
    raise TypeError(f"Tool {tool!r} is not callable.")
