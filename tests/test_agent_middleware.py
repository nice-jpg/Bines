from __future__ import annotations

from src.conductor.state import read_conductor_state
from src.device.services.execution import DeviceOperationExecutor
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import langgraph.checkpoint.memory  # noqa: F401 - preload real dependencies before local stubs.

import src.agent_runtime.engine  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import nTrace
import src.collector.delegation as _collector_dependency_preload  # noqa: E402,F401
import src.conductor.engine as _agent_dependency_preload  # noqa: E402,F401


class AgentMiddlewareWiringTests(unittest.TestCase):
    def test_build_agent_registers_request_compression_without_summarization(self) -> None:
        captured = {}

        def create_agent(**kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace()

        replacement = patch("langchain.agents.create_agent", create_agent)
        replacement.start()
        self.addCleanup(replacement.stop)

        agent = importlib.import_module("src.conductor.engine")
        agent.ConductorEngine(model="model", handle="test")

        middleware_names = [type(middleware).__name__ for middleware in captured["middleware"]]
        self.assertEqual(
            middleware_names,
            [
                "HostTraceMiddleware",
                "ModelRetryMiddleware",
                "ConversationInputMiddleware",
                "ForkContext",
                "ToolErrorMiddleware",
                "StrategyDispatchMiddleware",
                "ToolTraceMiddleware",
                "LLMTraceMiddleware",
            ],
        )
        dispatcher = captured["middleware"][5]
        general = dispatcher._resolve_strategy(read_conductor_state({"messages": []}))
        layers = {type(layer).__name__: layer for layer in general.middlewares}
        hitl = layers["CaptchaHumanInTheLoopMiddleware"]
        self.assertEqual(list(hitl.interrupt_on), ["authenticate_captcha"])
        self.assertEqual(hitl.interrupt_on["authenticate_captcha"]["allowed_decisions"], ["respond"])
        self.assertIn("checkpointer", captured)
        self.assertIn("is_done", [tool.name for tool in captured["tools"]])
        self.assertIn("get_probe_progress", [tool.name for tool in captured["tools"]])
        self.assertNotIn("authorize_probe_completion", [tool.name for tool in captured["tools"]])
        self.assertNotIn("notify_user", [tool.name for tool in captured["tools"]])
        self.assertIs(layers["PageVisitMiddleware"].mechanism, layers["PageInputMiddleware"].mechanism)
        self.assertEqual(
            list(layers),
            [
                "DangerousToolMiddleware",
                "CaptchaHumanInTheLoopMiddleware",
                "PageVisitMiddleware",
                "VisitHistoryMiddleware",
                "PageInputMiddleware",
                "OperationNoticeMiddleware",
                "DeviceOperationReliabilityMiddleware",
            ],
        )
        self.assertIn("confirm_page", [tool.name for tool in captured["tools"]])
        self.assertEqual(type(layers["OperationNoticeMiddleware"].service).__name__, "OperationNoticeTool")

    def test_captcha_hitl_middleware_factory_is_available_in_execution_module(self) -> None:
        self.test_build_agent_registers_request_compression_without_summarization()
        middleware = importlib.import_module("src.strategy.general.captcha")

        hitl = middleware.create_captcha_human_in_the_loop_middleware([], DeviceOperationExecutor())

        self.assertEqual(type(hitl).__name__, "CaptchaHumanInTheLoopMiddleware")
        self.assertEqual(list(hitl.interrupt_on), ["authenticate_captcha"])
        self.assertEqual(hitl.interrupt_on["authenticate_captcha"]["allowed_decisions"], ["respond"])
        self.assertEqual(middleware.CAPTCHA_AUTHENTICATION_TOOL_NAME, "authenticate_captcha")

    def test_captcha_middleware_starts_authentication_tool_once_before_interrupt(self) -> None:
        self.test_build_agent_registers_request_compression_without_summarization()
        middleware = importlib.import_module("src.strategy.general.captcha")

        calls = []

        class FakeTool:
            name = "authenticate_captcha"

            def invoke(self, args):
                calls.append(args)
                return "started"

        hitl = middleware.create_captcha_human_in_the_loop_middleware([FakeTool()], DeviceOperationExecutor())
        hitl._should_interrupt = lambda tool_call, config, state, runtime: True
        captcha_module = importlib.import_module("src.strategy.general.captcha")
        state = {
            "messages": [
                captcha_module.AIMessage(content=""),
            ]
        }
        state["messages"][0].tool_calls = [
            {
                "name": "authenticate_captcha",
                "args": {"current_path": "meituan/外卖", "reason": "captcha"},
                "id": "tool-call-1",
            }
        ]

        hitl._start_pending_authentication_sessions(state, runtime=object())
        hitl._start_pending_authentication_sessions(state, runtime=object())

        self.assertEqual(calls, [{"current_path": "meituan/外卖", "reason": "captcha"}])


if __name__ == "__main__":
    unittest.main()
