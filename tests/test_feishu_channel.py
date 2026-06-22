from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if "langchain_core.tools" not in sys.modules:
    langchain_core = types.ModuleType("langchain_core")
    langchain_core_tools = types.ModuleType("langchain_core.tools")

    class StructuredTool:
        @classmethod
        def from_function(cls, **kwargs):
            instance = cls()
            instance.name = kwargs["name"]
            instance.description = kwargs["description"]
            instance.func = kwargs["func"]
            return instance

        def invoke(self, values):
            return self.func(**values)

    langchain_core_tools.StructuredTool = StructuredTool
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.tools"] = langchain_core_tools

from channel.feishu.agent_bridge import FeishuAgentBridge
from channel.feishu.config import load_feishu_config
from channel.feishu.messages import IncomingMessage, parse_message_event


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    def reply_text(self, message_id: str, text: str) -> None:
        self.replies.append((message_id, text))


class FeishuChannelTests(unittest.TestCase):
    def test_config_loads_feishu_env_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "FEISHU_APP_ID=cli_test\nFEISHU_APP_SECRET=secret_test\nFEISHU_LOG_LEVEL=DEBUG\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = load_feishu_config(env_path)

        self.assertEqual(config.app_id, "cli_test")
        self.assertEqual(config.app_secret, "secret_test")
        self.assertEqual(config.log_level, "DEBUG")

    def test_config_loads_lark_and_app_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lark_env = Path(tmp_dir) / "lark.env"
            lark_env.write_text("LARK_APP_ID=cli_lark\nLARK_APP_SECRET=secret_lark\n", encoding="utf-8")
            app_env = Path(tmp_dir) / "app.env"
            app_env.write_text("APP_ID=cli_app\nAPP_SECRET=secret_app\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                lark_config = load_feishu_config(lark_env)
            with patch.dict(os.environ, {}, clear=True):
                app_config = load_feishu_config(app_env)

        self.assertEqual(lark_config.app_id, "cli_lark")
        self.assertEqual(lark_config.app_secret, "secret_lark")
        self.assertEqual(app_config.app_id, "cli_app")
        self.assertEqual(app_config.app_secret, "secret_app")

    def test_config_reports_missing_required_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "Missing required Feishu setting"):
                    load_feishu_config(env_path)

    def test_parse_text_message_event(self) -> None:
        data = _event_data(content='{"text":"hello"}')

        parsed = parse_message_event(data)

        self.assertTrue(parsed.ok)
        self.assertIsNotNone(parsed.incoming)
        self.assertEqual(parsed.incoming.text, "hello")
        self.assertEqual(parsed.incoming.chat_id, "chat_1")
        self.assertEqual(parsed.incoming.message_id, "msg_1")
        self.assertEqual(parsed.incoming.sender_open_id, "open_1")

    def test_parse_unsupported_and_malformed_messages(self) -> None:
        unsupported = parse_message_event(_event_data(message_type="image"))
        malformed = parse_message_event(_event_data(content="{bad-json"))

        self.assertFalse(unsupported.ok)
        self.assertIn("Unsupported message type", unsupported.error_text or "")
        self.assertFalse(malformed.ok)
        self.assertIn("Failed to parse", malformed.error_text or "")

    def test_agent_bridge_wraps_message_and_sends_final_output(self) -> None:
        messenger = FakeMessenger()
        captured = {}

        def runner(messages, tools):
            captured["messages"] = messages
            captured["tool_names"] = [tool.name for tool in tools]
            return "agent output"

        bridge = FeishuAgentBridge(messenger, agent_runner=runner)
        bridge.handle_message(_incoming(chat_type="group"))

        self.assertEqual(messenger.replies, [("msg_1", "agent output")])
        self.assertIn("notify_user", captured["tool_names"])
        self.assertEqual(captured["tool_names"].count("notify_user"), 1)
        self.assertIn("<feishu_message>", captured["messages"][-1]["content"])
        self.assertIn("<text>collect shops</text>", captured["messages"][-1]["content"])

    def test_agent_bridge_notify_user_sends_operation_notice_to_chat(self) -> None:
        messenger = FakeMessenger()

        def runner(_messages, tools):
            notify_tool = next(tool for tool in tools if tool.name == "notify_user")
            notify_tool.invoke(
                {
                    "operation": "tap '美食' on (320, 620) to load a subpage",
                    "current_path": "meituan",
                    "reason": "open configured secondary page",
                    "next_page": "美食",
                    "next_path": "meituan/美食",
                }
            )
            return "done"

        bridge = FeishuAgentBridge(messenger, agent_runner=runner)
        bridge.handle_message(_incoming(chat_type="p2p"))

        self.assertEqual(messenger.sent[-1], ("chat_1", "done"))
        self.assertTrue(any("Operation: tap '美食'" in text for _, text in messenger.sent))
        self.assertTrue(any("Next path: meituan/美食" in text for _, text in messenger.sent))


def _incoming(chat_type: str = "p2p") -> IncomingMessage:
    return IncomingMessage(
        chat_id="chat_1",
        message_id="msg_1",
        chat_type=chat_type,
        sender_open_id="open_1",
        text="collect shops",
    )


def _event_data(message_type: str = "text", content: str = '{"text":"hello"}'):
    return types.SimpleNamespace(
        event=types.SimpleNamespace(
            sender=types.SimpleNamespace(sender_id=types.SimpleNamespace(open_id="open_1")),
            message=types.SimpleNamespace(
                message_type=message_type,
                content=content,
                chat_id="chat_1",
                message_id="msg_1",
                chat_type="p2p",
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
