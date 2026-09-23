"""Run the Bines agent with the Feishu communication channel."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from src.agent import build_conductor_runtime
    from src.channel.feishu import FeishuChannelRuntime, load_feishu_config
    from src.model import build_model, close_model
    from src.tools.common import CaptchaAuthenticationTool, OperationNoticeTool, create_common_tools
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from agent import build_conductor_runtime
    from channel.feishu import FeishuChannelRuntime, load_feishu_config
    from model import build_model, close_model
    from tools.common import CaptchaAuthenticationTool, OperationNoticeTool, create_common_tools

CAPTCHA_RESUME_TEXT = "done"
LIVENESS_RESUME_TEXT = "继续"
MODEL_TRANSPORT_RESUME_TEXT = "继续"


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
    runtime = build_conductor_runtime(
        model=model,
        tools=create_common_tools(
            operation_notice_tool=operation_notice,
            captcha_authentication_tool=captcha_authentication,
        ),
        operation_notice_service=operation_notice,
    )

    def on_message(message) -> None:
        notifier.set(channel.build_notifier(message.target))
        session_id = f"feishu:{message.chat_id}"
        if runtime.has_pending_interrupt(session_id):
            interrupt_kind = runtime.pending_interrupt_kind(session_id)
            expected_text = (
                MODEL_TRANSPORT_RESUME_TEXT
                if interrupt_kind in {"model_transport", "execution"}
                else LIVENESS_RESUME_TEXT
                if interrupt_kind == "liveness"
                else CAPTCHA_RESUME_TEXT
            )
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
        close_model(model)


if __name__ == "__main__":
    main()
