"""Run the Bines agent with the Feishu communication channel."""

from __future__ import annotations

import argparse

try:
    from src.agent import AgentRuntime
    from src.channel.feishu import FeishuChannelRuntime, load_feishu_config
    from src.model import build_model
    from src.tools.common import OperationNoticeTool, create_common_tools
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from agent import AgentRuntime
    from channel.feishu import FeishuChannelRuntime, load_feishu_config
    from model import build_model
    from tools.common import OperationNoticeTool, create_common_tools

CAPTCHA_RESUME_TEXT = "验证码已完成"
CAPTCHA_PAUSED_MESSAGE = "验证码认证已暂停，请人工处理后回复 `验证码已完成`。"


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
    runtime = AgentRuntime(
        model=build_model(),
        tools=create_common_tools(operation_notice_tool=operation_notice),
        name="feishu-agent",
    )

    def on_message(message) -> None:
        notifier.set(channel.build_notifier(message.target))
        session_id = f"feishu:{message.chat_id}"
        if message.text == CAPTCHA_RESUME_TEXT and runtime.has_pending_interrupt(session_id):
            result = runtime.resume_turn(
                session_id=session_id,
                decisions=[{"type": "approve"}],
                max_iterations=args.max_iterations,
            )
        else:
            result = runtime.run_turn(
                [{"role": "user", "content": message.text}],
                session_id=session_id,
                max_iterations=args.max_iterations,
            )
        if result.interrupted:
            channel.send_text(message.target, CAPTCHA_PAUSED_MESSAGE)
            return
        channel.send_text(message.target, result.output)

    channel.start(on_message)


if __name__ == "__main__":
    main()
