"""Respond-only human-in-the-loop middleware for captcha authentication."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig

CAPTCHA_AUTHENTICATION_TOOL_NAME = "authenticate_captcha"
CAPTCHA_HITL_DESCRIPTION = (
    "Captcha authentication is required. Manually complete the captcha, "
    "then respond with the completion message so the agent can verify the UI again."
)


def create_captcha_human_in_the_loop_middleware() -> AgentMiddleware:
    """Create standard LangChain HITL middleware for human-as-tool captcha input."""

    config = _respond_interrupt_config()
    try:
        return HumanInTheLoopMiddleware(
            interrupt_on={CAPTCHA_AUTHENTICATION_TOOL_NAME: config},
            description_prefix="Captcha authentication requires human input",
        )
    except TypeError:
        # Lightweight unit-test stubs may not accept constructor args.
        middleware = HumanInTheLoopMiddleware()
        setattr(middleware, "interrupt_on", {CAPTCHA_AUTHENTICATION_TOOL_NAME: config})
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
