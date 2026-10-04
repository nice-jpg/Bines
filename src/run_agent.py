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

from langgraph.runtime import get_runtime
from src.agent_runtime.communication import talk
from src.feishu_agent_runner import FeishuAgentRunner


def notify_current_session(text: str) -> bool:
    return talk(text, get_runtime())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Bines agent through Feishu.")
    parser.add_argument("--max-iterations", type=int, default=1000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    channel = FeishuChannelRuntime(load_feishu_config())
    operation_notice = OperationNoticeTool(notifier=notify_current_session)
    captcha_authentication = CaptchaAuthenticationTool(notifier=notify_current_session)
    model = build_model()
    runtime = build_conductor_runtime(
        model=model,
        tools=create_common_tools(
            operation_notice_tool=operation_notice,
            captcha_authentication_tool=captcha_authentication,
        ),
        operation_notice_service=operation_notice,
    )

    runner = FeishuAgentRunner(runtime, channel, args.max_iterations)
    try:
        channel.start(runner.on_message)
    finally:
        runner.close()
        close_model(model)


if __name__ == "__main__":
    main()
