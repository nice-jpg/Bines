from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class RunAgentEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._module_names = [
            "run_agent",
            "src.agent",
            "src.channel.feishu",
            "src.model",
            "src.tools.common",
            "agent",
            "channel.feishu",
            "model",
            "tools.common",
        ]
        self._original_modules = {name: sys.modules.get(name) for name in self._module_names}

    def tearDown(self) -> None:
        for name, module in self._original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_main_initializes_runtime_once_and_runs_each_feishu_message(self) -> None:
        calls = {"runtime_init": 0, "run_turn": [], "sent": [], "notices": []}

        class FakeTarget:
            def __init__(self, chat_id):
                self.chat_id = chat_id

        class FakeMessage:
            def __init__(self, chat_id):
                self.chat_id = chat_id
                self.text = f"text from {chat_id}"
                self.target = FakeTarget(chat_id)

        class FakeChannel:
            def __init__(self, _config):
                pass

            def build_notifier(self, target):
                return lambda text: calls["notices"].append((target.chat_id, text))

            def send_text(self, target, text):
                calls["sent"].append((target.chat_id, text))

            def start(self, on_message):
                on_message(FakeMessage("chat_1"))
                on_message(FakeMessage("chat_2"))

        class FakeRuntime:
            def __init__(self, *, model, tools, name=None):
                calls["runtime_init"] += 1
                self.tools = tools

            def run_turn(self, messages, *, session_id=None, max_iterations):
                calls["run_turn"].append((messages, session_id, max_iterations))
                operation_notice = self.tools[0]
                operation_notice.notifier("notice")
                return types.SimpleNamespace(output=f"output_{len(calls['run_turn'])}", interrupted=False)

        class FakeOperationNoticeTool:
            def __init__(self, notifier):
                self.notifier = notifier

        class FakeCaptchaAuthenticationTool:
            def __init__(self, notifier):
                self.notifier = notifier

        fake_agent = types.ModuleType("src.agent")
        fake_agent.AgentRuntime = FakeRuntime
        fake_channel = types.ModuleType("src.channel.feishu")
        fake_channel.FeishuChannelRuntime = FakeChannel
        fake_channel.load_feishu_config = lambda: "config"
        fake_model = types.ModuleType("src.model")
        fake_model.build_model = lambda: "model"
        fake_tools_common = types.ModuleType("src.tools.common")
        fake_tools_common.CaptchaAuthenticationTool = FakeCaptchaAuthenticationTool
        fake_tools_common.OperationNoticeTool = FakeOperationNoticeTool
        fake_tools_common.create_common_tools = lambda operation_notice_tool, captcha_authentication_tool: [
            operation_notice_tool,
            captcha_authentication_tool,
        ]

        sys.modules["src.agent"] = fake_agent
        sys.modules["src.channel.feishu"] = fake_channel
        sys.modules["src.model"] = fake_model
        sys.modules["src.tools.common"] = fake_tools_common
        sys.modules["tools.common"] = fake_tools_common
        sys.modules.pop("run_agent", None)

        run_agent = importlib.import_module("run_agent")
        old_argv = sys.argv
        try:
            sys.argv = ["run_agent.py"]
            run_agent.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(calls["runtime_init"], 1)
        self.assertEqual(len(calls["run_turn"]), 2)
        self.assertEqual([item[1] for item in calls["run_turn"]], ["feishu:chat_1", "feishu:chat_2"])
        self.assertEqual([item[2] for item in calls["run_turn"]], [1000, 1000])
        self.assertEqual(calls["run_turn"][0][0], [{"role": "user", "content": "text from chat_1"}])
        self.assertNotIn("chat_id", str(calls["run_turn"][0][0]))
        self.assertEqual(calls["sent"], [("chat_1", "output_1"), ("chat_2", "output_2")])
        self.assertEqual(calls["notices"], [("chat_1", "notice"), ("chat_2", "notice")])

    def test_done_message_resumes_pending_workflow_without_runner_captcha_coupling(self) -> None:
        calls = {"run_turn": [], "resume_turn": [], "sent": []}

        class FakeTarget:
            def __init__(self, chat_id):
                self.chat_id = chat_id

        class FakeMessage:
            def __init__(self, chat_id, text):
                self.chat_id = chat_id
                self.text = text
                self.target = FakeTarget(chat_id)

        class FakeChannel:
            def __init__(self, _config):
                pass

            def build_notifier(self, _target):
                return lambda _text: None

            def send_text(self, target, text):
                calls["sent"].append((target.chat_id, text))

            def start(self, on_message):
                on_message(FakeMessage("chat_1", "start captcha task"))
                on_message(FakeMessage("chat_1", "done"))
                on_message(FakeMessage("chat_2", "done"))

        class FakeRuntime:
            def __init__(self, *, model, tools, name=None):
                self.pending = {"feishu:chat_1": False}

            def has_pending_interrupt(self, session_id):
                return self.pending.get(session_id, False)

            def run_turn(self, messages, *, session_id=None, max_iterations):
                calls["run_turn"].append((messages, session_id, max_iterations))
                if session_id == "feishu:chat_1":
                    self.pending[session_id] = True
                    return types.SimpleNamespace(output="", interrupted=True)
                return types.SimpleNamespace(output="normal output", interrupted=False)

            def resume_turn(self, *, session_id, user_input, max_iterations):
                calls["resume_turn"].append((session_id, user_input, max_iterations))
                self.pending[session_id] = False
                return types.SimpleNamespace(output="resumed output", interrupted=False)

        class FakeOperationNoticeTool:
            def __init__(self, notifier):
                self.notifier = notifier

        class FakeCaptchaAuthenticationTool:
            def __init__(self, notifier):
                self.notifier = notifier

        fake_agent = types.ModuleType("src.agent")
        fake_agent.AgentRuntime = FakeRuntime
        fake_channel = types.ModuleType("src.channel.feishu")
        fake_channel.FeishuChannelRuntime = FakeChannel
        fake_channel.load_feishu_config = lambda: "config"
        fake_model = types.ModuleType("src.model")
        fake_model.build_model = lambda: "model"
        fake_tools_common = types.ModuleType("src.tools.common")
        fake_tools_common.CaptchaAuthenticationTool = FakeCaptchaAuthenticationTool
        fake_tools_common.OperationNoticeTool = FakeOperationNoticeTool
        fake_tools_common.create_common_tools = lambda operation_notice_tool, captcha_authentication_tool: [
            operation_notice_tool,
            captcha_authentication_tool,
        ]

        sys.modules["src.agent"] = fake_agent
        sys.modules["src.channel.feishu"] = fake_channel
        sys.modules["src.model"] = fake_model
        sys.modules["src.tools.common"] = fake_tools_common
        sys.modules["tools.common"] = fake_tools_common
        sys.modules.pop("run_agent", None)

        run_agent = importlib.import_module("run_agent")
        old_argv = sys.argv
        try:
            sys.argv = ["run_agent.py"]
            run_agent.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(calls["run_turn"][0][0], [{"role": "user", "content": "start captcha task"}])
        self.assertEqual(calls["run_turn"][1][0], [{"role": "user", "content": "done"}])
        self.assertEqual([item[1] for item in calls["run_turn"]], ["feishu:chat_1", "feishu:chat_2"])
        self.assertEqual(calls["resume_turn"], [("feishu:chat_1", "done", 1000)])
        self.assertEqual(
            calls["sent"],
            [
                ("chat_1", "resumed output"),
                ("chat_2", "normal output"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
