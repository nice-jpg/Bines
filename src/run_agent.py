"""Assemble the channel and its dialogue agent before starting the application."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel

from src.agent_hypervisor.host import Host
from src.application.assistant import AssistantEngine
from src.application.model import build_model, close_model
from src.channel.feishu import FeishuChannel, load_feishu_config
from src.common.tracing import trace_client
from src.configuration.manager import ConfigurationManager


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Run the Bines agent through Feishu."
    )
    parser.add_argument("--max-iterations", type=int, default=1000)
    return parser


def main() -> None:
    args: argparse.Namespace = build_parser().parse_args()
    ConfigurationManager.initialize()
    trace_client()
    channel: FeishuChannel = FeishuChannel(load_feishu_config(), "feishu")
    model: BaseChatModel | None = None
    try:
        model = build_model()
        host = Host()
        assistant = AssistantEngine(
            model=model, handle=host.register_root(), max_iterations=args.max_iterations
        )
        asyncio.run(host.run(assistant, [channel]))
    except KeyboardInterrupt:
        pass
    finally:
        if model is not None:
            close_model(model)


if __name__ == "__main__":
    main()
