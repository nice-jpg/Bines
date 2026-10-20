from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.conductor.engine import (
    APP_PROBE_TRIGGER,
    build_app_probe_messages,
    messages_request_app_probe,
)

from src.prompts import build_probe_task_spec


class AppProbeTaskTests(unittest.TestCase):
    def test_app_probe_trigger_requires_exact_match(self) -> None:
        self.assertTrue(messages_request_app_probe([{"role": "user", "content": APP_PROBE_TRIGGER}]))
        self.assertFalse(messages_request_app_probe([{"role": "user", "content": f" {APP_PROBE_TRIGGER}"}]))
        self.assertFalse(messages_request_app_probe([{"role": "user", "content": f"{APP_PROBE_TRIGGER} "}]))
        self.assertFalse(messages_request_app_probe([{"role": "user", "content": f"{APP_PROBE_TRIGGER}。"}]))
        self.assertFalse(messages_request_app_probe([{"role": "user", "content": "普通聊天"}]))

    def test_messages_request_app_probe_uses_only_plain_exact_current_text(self) -> None:
        self.assertTrue(messages_request_app_probe([{"role": "user", "content": APP_PROBE_TRIGGER}]))
        self.assertFalse(
            messages_request_app_probe(
                [{"role": "user", "content": f"<feishu_message><text>{APP_PROBE_TRIGGER}</text></feishu_message>"}]
            )
        )
        self.assertFalse(messages_request_app_probe([{"role": "user", "content": f"{APP_PROBE_TRIGGER} "}]))

    def test_build_app_probe_messages_wraps_existing_initial_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<package name="com.sankuai.meituan">
    <city name="安徽省宿州市">
        <args>
            <address name="万成哈弗国际A区1栋"/>
            <range size="1000"/>
        </args>
        <subpage name="美食"/>
    </city>
</package>
""",
                encoding="utf-8",
            )

            with patch("src.prompts.initial_message.WORKSPACE_ROOT", workspace):
                with patch("src.prompts.initial_message.date") as clock:
                    clock.today.return_value = date.fromisoformat("2026-06-22")
                    messages = build_app_probe_messages()

        self.assertEqual(len(messages), 2)
        self.assertIn("<environment_context>", messages[0]["content"])
        self.assertIn("<config_context>", messages[1]["content"])
        self.assertIn("- 应用包名：com.sankuai.meituan", messages[1]["content"])

    def test_probe_task_spec_builds_absolute_leaf_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<package name="com.sankuai.meituan">
    <city name="安徽省宿州市">
        <args><address name="A"/><range size="1"/></args>
        <subpage name="外卖"><leaf name="美食"/></subpage>
    </city>
</package>
""",
                encoding="utf-8",
            )

            with patch("src.prompts.initial_message.WORKSPACE_ROOT", workspace):
                spec = build_probe_task_spec()

        self.assertEqual(spec.package_name, "com.sankuai.meituan")
        self.assertEqual(spec.target_leaf_paths, ("meituan/外卖/美食",))


if __name__ == "__main__":
    unittest.main()


def test_channel_probe_trigger_initializes_task_and_preserves_dialogue():
    from langchain_core.messages import AIMessage, HumanMessage
    from engine_helpers import Model, ScriptedEngine
    from gateway_helpers import GatewayHandle, RecordingChannel
    from src.agent_runtime.types.execution import EngineResult, EngineStatus
    from src.prompts.initial_message import ProbeTaskSpec

    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    engine = ScriptedEngine(model=Model(replies=[AIMessage(content="ok")]), gateway_id=gateway.gateway_id)
    channel.receive("chat", APP_PROBE_TRIGGER, sender="alice")
    channel.receive("chat", "先查看首页", sender="bob")
    with (
        patch("src.conductor.engine.build_probe_task_spec", return_value=ProbeTaskSpec("app", ("app/home",))),
        patch("src.conductor.engine.build_app_probe_messages", return_value=[{"role": "user", "content": "task config"}]),
        patch.object(engine, "_execute", return_value=EngineResult(EngineStatus.COMPLETED)) as execute,
    ):
        engine.run_turn()
        state = execute.call_args.args[0]
        assert state["probe"]["run_id"]
        assert state["probe"]["package_name"] == "app"
        assert state["probe"]["target_leaf_paths"] == ("app/home",)
        assert state["messages"][0]["content"] == "task config"
        message = state["messages"][-1]
        assert isinstance(message, HumanMessage)
        assert f"alice: {APP_PROBE_TRIGGER}\nbob: 先查看首页" in message.content
        assert not engine._requests_app_probe([HumanMessage(content="unrelated")])
        engine._state = state
        engine.conversation.acknowledge(message.id)
        channel.receive("chat", "继续查看")
        engine.run_turn()
        assert execute.call_args.args[0]["probe"]["run_id"] == state["probe"]["run_id"]
    engine.close()


def test_channel_trigger_does_not_parse_formatted_or_quoted_commands():
    from langchain_core.messages import AIMessage
    from engine_helpers import Model, ScriptedEngine
    from gateway_helpers import GatewayHandle, RecordingChannel
    from src.agent_runtime.types.execution import EngineResult, EngineStatus

    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    engine = ScriptedEngine(model=Model(replies=[AIMessage(content="ok")]), gateway_id=gateway.gateway_id)
    channel.receive("chat", f"引用：\nalice: {APP_PROBE_TRIGGER}", name=APP_PROBE_TRIGGER)
    with patch.object(engine, "_execute", return_value=EngineResult(EngineStatus.COMPLETED)) as execute:
        engine.run_turn()
        assert execute.call_args.args[0]["probe"] == {}
    engine.close()
