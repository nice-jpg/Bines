"""Run the Bines agent with the Feishu communication channel."""

from __future__ import annotations

import argparse

try:
    from src.agent import AgentRuntime
    from src.channel.feishu import FeishuChannelRuntime, load_feishu_config
    from src.model import build_model
    from src.tools.common import CaptchaAuthenticationTool, OperationNoticeTool, create_common_tools
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from agent import AgentRuntime
    from channel.feishu import FeishuChannelRuntime, load_feishu_config
    from model import build_model
    from tools.common import CaptchaAuthenticationTool, OperationNoticeTool, create_common_tools

CAPTCHA_RESUME_TEXT = "done"
LIVENESS_RESUME_TEXT = "继续"


class MutableNotifier:
    """A notifier proxy whose destination can change per inbound message."""

    def __init__(self) -> None:
        self._notifier = None

    def set(self, notifier) -> None:
        self._notifier = notifier

    def __call__(self, text: str) -> None:
        if self._notifier is not None:
            self._notifier(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Bines agent through Feishu.")
    parser.add_argument("--max-iterations", type=int, default=1000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    channel = FeishuChannelRuntime(load_feishu_config())
    notifier = MutableNotifier()
    operation_notice = OperationNoticeTool(notifier=notifier)
    captcha_authentication = CaptchaAuthenticationTool(notifier=notifier)
    model = build_model()
    runtime = AgentRuntime(
        model=model,
        tools=create_common_tools(
            operation_notice_tool=operation_notice,
            captcha_authentication_tool=captcha_authentication,
        ),
        name="main",
    )

    def on_message(message) -> None:
        notifier.set(channel.build_notifier(message.target))
        session_id = f"feishu:{message.chat_id}"
        has_pending = getattr(runtime, "has_pending_interrupt", lambda _session_id: False)
        if has_pending(session_id):
            interrupt_kind_getter = getattr(runtime, "pending_interrupt_kind", lambda _session_id: "human")
            interrupt_kind = interrupt_kind_getter(session_id)
            expected_text = LIVENESS_RESUME_TEXT if interrupt_kind == "liveness" else CAPTCHA_RESUME_TEXT
            if message.text != expected_text:
                channel.send_text(
                    message.target,
                    f"当前任务等待恢复。请回复“{expected_text}”继续。",
                )
                return
            result = runtime.resume_turn(
                session_id=session_id,
                user_input=message.text,
                max_iterations=args.max_iterations,
            )
        else:
            result = runtime.run_turn(
                [{"role": "user", "content": message.text}],
                session_id=session_id,
                max_iterations=args.max_iterations,
            )
        if result.output:
            channel.send_text(message.target, result.output)

    try:
        channel.start(on_message)
    finally:
        close_model = getattr(model, "close", None)
        if callable(close_model):
            close_model()


if __name__ == "__main__":
    main()
