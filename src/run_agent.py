"""Assemble one engine, coordinator, worker and ID-addressed gateway."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel

from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.worker import AgentWorker
from src.application.gateway_manager import GatewayManager
from src.application.model import build_model, close_model
from src.application.trace import trace_client
from src.channel.feishu import FeishuChannel, load_feishu_config
from src.conductor.engine import ConductorEngine
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
    manager: GatewayManager = GatewayManager.instance()
    gateway_id: str = manager.create(channel)
    model: BaseChatModel | None = None
    worker: AgentWorker | None = None
    engine: ConductorEngine | None = None
    started: bool = False
    try:
        model = build_model()
        engine = ConductorEngine(model=model, gateway_id=gateway_id, max_iterations=args.max_iterations)
        worker = AgentWorker(AgentCoordinator(engine))
        manager.start(gateway_id)
        worker.start()
        started = True
        try:
            worker.join()
        except KeyboardInterrupt:
            pass
    finally:
        manager.stop(gateway_id)
        try:
            if worker is not None and started:
                worker.join()
            elif engine is not None:
                engine.close()
        finally:
            manager.finish_output(gateway_id)
            manager.join(gateway_id)
            try:
                if model is not None:
                    close_model(model)
            finally:
                manager.release(gateway_id)


if __name__ == "__main__":
    main()
