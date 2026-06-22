from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class AgentRuntimeTests(unittest.TestCase):
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

    def test_agent_runtime_reuses_one_agent_for_multiple_turns(self) -> None:
        captured = {"create_count": 0, "invocations": []}
        self._install_langchain_stubs(captured)
        sys.modules.pop("agent", None)
        agent = importlib.import_module("agent")
        agent.collect_tools = lambda **_kwargs: []

        runtime = agent.AgentRuntime(model="model", tools=[], name="runtime")
        first = runtime.run_turn([{"role": "user", "content": "hello"}])
        second = runtime.run_turn([{"role": "user", "content": "again"}])

        self.assertEqual(captured["create_count"], 1)
        self.assertEqual(first.output, "agent output")
        self.assertEqual(second.output, "agent output")
        self.assertEqual(len(captured["invocations"]), 2)
        self.assertNotIn("<environment_context>", str(captured["invocations"][0]))
        self.assertNotIn("<config_context>", str(captured["invocations"][0]))

    def test_run_agent_loop_compatibility_wrapper_returns_result(self) -> None:
        captured = {"create_count": 0, "invocations": []}
        self._install_langchain_stubs(captured)
        sys.modules.pop("agent", None)
        agent = importlib.import_module("agent")
        agent.collect_tools = lambda **_kwargs: []

        result = agent.run_agent_loop(
            model="model",
            tools=[],
            history=[{"role": "user", "content": "hello"}],
        )

        self.assertEqual(result.output, "agent output")
        self.assertEqual(captured["create_count"], 1)

    def _install_langchain_stubs(self, captured) -> None:
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

        class FakeAgent:
            def invoke(self, payload, config=None):
                captured.setdefault("configs", []).append(config)
                if not isinstance(payload, dict):
                    captured.setdefault("commands", []).append(payload)
                    return {
                        "messages": [
                            {"role": "assistant", "content": "resumed output"},
                        ]
                    }
                captured["invocations"].append(payload["messages"])
                if captured.get("interrupt_next"):
                    captured["interrupt_next"] = False
                    return {
                        "__interrupt__": ["captcha approval required"],
                        "messages": payload["messages"],
                    }
                return {
                    "messages": [
                        *payload["messages"],
                        {"role": "assistant", "content": "agent output"},
                    ]
                }

        def create_agent(**_kwargs):
            captured["create_count"] += 1
            return FakeAgent()

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

    def test_agent_runtime_preserves_history_per_session(self) -> None:
        captured = {"create_count": 0, "invocations": []}
        self._install_langchain_stubs(captured)
        sys.modules.pop("agent", None)
        agent = importlib.import_module("agent")
        agent.collect_tools = lambda **_kwargs: []

        runtime = agent.AgentRuntime(model="model", tools=[], name="runtime")
        runtime.run_turn([{"role": "user", "content": "first"}], session_id="s1")
        runtime.run_turn([{"role": "user", "content": "second"}], session_id="s1")
        runtime.run_turn([{"role": "user", "content": "other"}], session_id="s2")

        self.assertEqual([message["content"] for message in captured["invocations"][1]], ["first", "agent output", "second"])
        self.assertEqual([message["content"] for message in captured["invocations"][2]], ["other"])

    def test_app_probe_context_only_uses_current_turn_not_session_history(self) -> None:
        captured = {"create_count": 0, "invocations": []}
        self._install_langchain_stubs(captured)
        sys.modules.pop("agent", None)
        agent = importlib.import_module("agent")
        agent.collect_tools = lambda **_kwargs: []
        agent.build_app_probe_messages = lambda: [{"role": "user", "content": "<environment_context>probe</environment_context>"}]

        runtime = agent.AgentRuntime(model="model", tools=[], name="runtime")
        runtime.run_turn([{"role": "user", "content": "执行应用探测任务"}], session_id="s1")
        runtime.run_turn([{"role": "user", "content": "普通消息"}], session_id="s1")

        first_invocation = str(captured["invocations"][0])
        second_new_tail = captured["invocations"][1][-1]["content"]
        self.assertIn("<environment_context>probe</environment_context>", first_invocation)
        self.assertEqual(second_new_tail, "普通消息")

    def test_interrupted_state_marks_result_and_can_resume_same_session(self) -> None:
        captured = {"create_count": 0, "invocations": [], "interrupt_next": True}
        self._install_langchain_stubs(captured)
        sys.modules.pop("agent", None)
        agent = importlib.import_module("agent")
        agent.collect_tools = lambda **_kwargs: []

        runtime = agent.AgentRuntime(model="model", tools=[], name="runtime")
        interrupted = runtime.run_turn([{"role": "user", "content": "captcha"}], session_id="s1")
        resumed = runtime.resume_turn(session_id="s1", decisions=[{"type": "approve"}])

        self.assertTrue(interrupted.interrupted)
        self.assertEqual(interrupted.interrupts, ["captcha approval required"])
        self.assertFalse(resumed.interrupted)
        self.assertEqual(resumed.output, "resumed output")
        self.assertTrue(runtime.has_pending_interrupt("s1") is False)
        self.assertEqual(captured["configs"][0]["configurable"]["thread_id"], "s1")
        self.assertEqual(captured["configs"][1]["configurable"]["thread_id"], "s1")
        self.assertEqual(captured["commands"][0].kwargs["resume"]["decisions"], [{"type": "approve"}])


if __name__ == "__main__":
    unittest.main()
