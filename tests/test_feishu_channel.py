from __future__ import annotations

import json
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

from channel.feishu.config import FeishuConfig, load_feishu_config
from channel.feishu.dedup import MessageDeduplicator
from channel.feishu.messages import IncomingMessage, MessageTarget, parse_message_event
from channel.feishu.receiver import FeishuChannelRuntime


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

    def test_parse_text_message_event_preserves_metadata_and_mentions(self) -> None:
        mentions = [{"key": "@_user_1", "id": {"open_id": "mentioned_open"}, "name": "user1"}]
        data = _event_data(content='{"text":"hello @user1"}', mentions=mentions)

        parsed = parse_message_event(data)

        self.assertTrue(parsed.ok)
        self.assertIsNotNone(parsed.incoming)
        incoming = parsed.incoming
        self.assertEqual(incoming.text, "hello @user1")
        self.assertEqual(incoming.chat_id, "chat_1")
        self.assertEqual(incoming.message_id, "msg_1")
        self.assertEqual(incoming.root_id, "root_1")
        self.assertEqual(incoming.parent_id, "parent_1")
        self.assertEqual(incoming.create_time, "1710000000000")
        self.assertEqual(incoming.update_time, "1710000001000")
        self.assertEqual(incoming.sender_open_id, "open_1")
        self.assertEqual(incoming.sender_union_id, "union_1")
        self.assertEqual(incoming.sender_user_id, "user_1")
        self.assertEqual(incoming.mentions, mentions)
        self.assertIs(incoming.raw_message, data.event.message)

    def test_parse_unsupported_and_malformed_messages_keep_message_identity(self) -> None:
        unsupported = parse_message_event(_event_data(message_type="image"))
        malformed = parse_message_event(_event_data(content="{bad-json"))

        self.assertFalse(unsupported.ok)
        self.assertIsNotNone(unsupported.incoming)
        self.assertEqual(unsupported.incoming.message_id, "msg_1")
        self.assertEqual(unsupported.incoming.message_type, "image")
        self.assertIn("Unsupported message type", unsupported.error_text or "")
        self.assertFalse(malformed.ok)
        self.assertIsNotNone(malformed.incoming)
        self.assertEqual(malformed.incoming.chat_id, "chat_1")
        self.assertIn("Failed to parse", malformed.error_text or "")

    def test_message_deduplicator_filters_repeated_ids_and_enforces_lru(self) -> None:
        dedup = MessageDeduplicator(capacity=2, persistence_path=None)

        self.assertTrue(dedup.should_process("msg_1"))
        self.assertFalse(dedup.should_process("msg_1"))
        self.assertTrue(dedup.should_process("msg_2"))
        self.assertTrue(dedup.should_process("msg_3"))
        self.assertTrue(dedup.should_process("msg_1"))

    def test_message_deduplicator_restores_persisted_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "processed.jsonl"
            path.write_text(json.dumps({"message_id": "msg_1", "chat_id": "chat_1"}) + "\n", encoding="utf-8")

            dedup = MessageDeduplicator(capacity=10, persistence_path=path)

            self.assertFalse(dedup.should_process("msg_1", chat_id="chat_1"))
            self.assertTrue(dedup.should_process("msg_2", chat_id="chat_1"))

    def test_channel_runtime_deduplicates_before_callback(self) -> None:
        received = []
        errors = []
        runtime = FeishuChannelRuntime(
            FeishuConfig("app", "secret"),
            messenger=FakeMessenger(),
            deduplicator=MessageDeduplicator(persistence_path=None),
            on_parse_error=lambda parsed, _data: errors.append(parsed),
        )
        runtime._on_message = received.append

        runtime._handle_message(_event_data())
        runtime._handle_message(_event_data())
        runtime._handle_message(_event_data(message_type="image", message_id="msg_2"))

        self.assertEqual([message.message_id for message in received], ["msg_1"])
        self.assertEqual([error.incoming.message_id for error in errors if error.incoming], ["msg_2"])

    def test_channel_runtime_sends_to_p2p_or_replies_to_group(self) -> None:
        messenger = FakeMessenger()
        runtime = FeishuChannelRuntime(
            FeishuConfig("app", "secret"),
            messenger=messenger,
            deduplicator=MessageDeduplicator(persistence_path=None),
        )

        runtime.send_text(MessageTarget("chat_1", "msg_1", "p2p"), "p2p output")
        runtime.send_text(MessageTarget("chat_2", "msg_2", "group"), "group output")
        runtime.build_notifier(MessageTarget("chat_3", "msg_3", "group"))("notice")

        self.assertEqual(messenger.sent, [("chat_1", "p2p output"), ("chat_3", "notice")])
        self.assertEqual(messenger.replies, [("msg_2", "group output")])

    def test_transport_modules_do_not_depend_on_agent_runtime(self) -> None:
        feishu_dir = Path(__file__).resolve().parents[1] / "src" / "channel" / "feishu"
        forbidden = ("src.agent", "src.model", "src.tools", "src.prompts")
        transport_files = [
            path
            for path in feishu_dir.glob("*.py")
        ]

        for path in transport_files:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertFalse(any(item in content for item in forbidden))


def _incoming(chat_type: str = "p2p") -> IncomingMessage:
    return IncomingMessage(
        message_id="msg_1",
        root_id="root_1",
        parent_id="parent_1",
        chat_id="chat_1",
        chat_type=chat_type,
        message_type="text",
        create_time="1710000000000",
        update_time="1710000001000",
        sender_open_id="open_1",
        sender_union_id="union_1",
        sender_user_id="user_1",
        text="collect & shops",
        mentions=[{"id": {"open_id": "mentioned_open"}, "name": "user1"}],
    )


def _event_data(
    message_type: str = "text",
    content: str = '{"text":"hello"}',
    mentions=None,
    message_id: str = "msg_1",
):
    return types.SimpleNamespace(
        event=types.SimpleNamespace(
            sender=types.SimpleNamespace(
                sender_id=types.SimpleNamespace(
                    open_id="open_1",
                    union_id="union_1",
                    user_id="user_1",
                )
            ),
            message=types.SimpleNamespace(
                message_id=message_id,
                root_id="root_1",
                parent_id="parent_1",
                message_type=message_type,
                content=content,
                mentions=mentions or [],
                chat_id="chat_1",
                chat_type="p2p",
                create_time="1710000000000",
                update_time="1710000001000",
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
