from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class AgentMiddlewareWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_modules = {
            name: sys.modules.get(name)
            for name in (
                "agent",
                "langchain",
                "langchain.agents",
                "langchain.agents.middleware",
                "langchain.agents.middleware.summarization",
                "langchain.agents.middleware.types",
                "langchain_core",
                "langchain_core.language_models",
                "langchain_core.language_models.chat_models",
                "langchain_core.messages",
                "langchain_core.tools",
            )
        }

    def tearDown(self) -> None:
        for name, module in self._original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_build_agent_registers_context_compression_before_summarization(self) -> None:
        captured = {}

        langchain = types.ModuleType("langchain")
        langchain_agents = types.ModuleType("langchain.agents")
        langchain_agents_middleware = types.ModuleType("langchain.agents.middleware")
        langchain_agents_middleware_summarization = types.ModuleType("langchain.agents.middleware.summarization")
        langchain_agents_middleware_types = types.ModuleType("langchain.agents.middleware.types")
        langchain_core = types.ModuleType("langchain_core")
        langchain_core_language_models = types.ModuleType("langchain_core.language_models")
        langchain_core_chat_models = types.ModuleType("langchain_core.language_models.chat_models")
        langchain_core_messages = types.ModuleType("langchain_core.messages")
        langchain_core_tools = types.ModuleType("langchain_core.tools")

        class AgentMiddleware:
            pass

        class BaseChatModel:
            pass

        class BaseMessage:
            pass

        class StructuredTool:
            @classmethod
            def from_function(cls, **kwargs):
                instance = cls()
                instance.name = kwargs["name"]
                instance.description = kwargs["description"]
                instance.func = kwargs["func"]
                return instance

        class SummarizationMiddleware(AgentMiddleware):
            def __init__(self, model):
                self.model = model

        class TodoListMiddleware(AgentMiddleware):
            pass

        class HumanInTheLoopMiddleware(AgentMiddleware):
            pass

        class InterruptOnConfig:
            pass

        def create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        langchain_agents.create_agent = create_agent
        langchain_agents_middleware.HumanInTheLoopMiddleware = HumanInTheLoopMiddleware
        langchain_agents_middleware.InterruptOnConfig = InterruptOnConfig
        langchain_agents_middleware.TodoListMiddleware = TodoListMiddleware
        langchain_agents_middleware_summarization.SummarizationMiddleware = SummarizationMiddleware
        langchain_agents_middleware_types.AgentMiddleware = AgentMiddleware
        langchain_core_chat_models.BaseChatModel = BaseChatModel
        langchain_core_messages.BaseMessage = BaseMessage
        langchain_core_tools.StructuredTool = StructuredTool

        sys.modules["langchain"] = langchain
        sys.modules["langchain.agents"] = langchain_agents
        sys.modules["langchain.agents.middleware"] = langchain_agents_middleware
        sys.modules["langchain.agents.middleware.summarization"] = langchain_agents_middleware_summarization
        sys.modules["langchain.agents.middleware.types"] = langchain_agents_middleware_types
        sys.modules["langchain_core"] = langchain_core
        sys.modules["langchain_core.language_models"] = langchain_core_language_models
        sys.modules["langchain_core.language_models.chat_models"] = langchain_core_chat_models
        sys.modules["langchain_core.messages"] = langchain_core_messages
        sys.modules["langchain_core.tools"] = langchain_core_tools
        sys.modules.pop("agent", None)

        agent = importlib.import_module("agent")
        agent.collect_tools = lambda: []

        agent.build_agent(model="model", tools=[])

        middleware_names = [type(middleware).__name__ for middleware in captured["middleware"]]
        self.assertEqual(
            middleware_names,
            ["DeviceContextCompressionMiddleware", "SummarizationMiddleware"],
        )


if __name__ == "__main__":
    unittest.main()
