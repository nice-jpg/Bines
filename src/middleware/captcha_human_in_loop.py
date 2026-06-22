"""Human-in-the-loop middleware for captcha authentication."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain.agents.middleware.types import AgentMiddleware

CAPTCHA_AUTHENTICATION_TOOL_NAME = "authenticate_captcha"
CAPTCHA_HITL_DESCRIPTION = (
    "Captcha authentication is required. Manually complete the captcha, "
    "then approve this tool call so the agent can verify the UI again."
)


def create_captcha_human_in_the_loop_middleware() -> AgentMiddleware:
    """Create the HITL middleware that interrupts only captcha authentication."""

    config = _captcha_interrupt_config()
    try:
        return HumanInTheLoopMiddleware(
            interrupt_on={CAPTCHA_AUTHENTICATION_TOOL_NAME: config},
            description_prefix="Captcha authentication requires human approval",
        )
    except TypeError:
        # Lightweight unit-test stubs may not accept constructor args.
        middleware = HumanInTheLoopMiddleware()
        setattr(middleware, "interrupt_on", {CAPTCHA_AUTHENTICATION_TOOL_NAME: config})
        return middleware


def _captcha_interrupt_config() -> Any:
    try:
        return InterruptOnConfig(
            allowed_decisions=["approve", "reject"],
            description=CAPTCHA_HITL_DESCRIPTION,
        )
    except TypeError:
        return {
            "allowed_decisions": ["approve", "reject"],
            "description": CAPTCHA_HITL_DESCRIPTION,
        }
