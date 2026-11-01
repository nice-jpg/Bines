from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from src.configuration.loader import load_configuration
from src.configuration.manager import ConfigurationManager
from src.configuration.errors import ConfigurationError

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.channel.feishu.config import FeishuConfig, load_feishu_config
from src.channel.feishu.dedup import MessageDeduplicator
from src.channel.feishu.channel import FeishuChannel
from lark_oapi.channel.types import InboundMessage, Conversation, Identity, TextContent


@contextmanager
def configured_environment(env_path):
    with patch("src.configuration.loader.WORKSPACE_ROOT", env_path.parent):
        # Alias examples use different filenames; the service always reads .env.
        if env_path.name != ".env":
            (env_path.parent / ".env").write_text(env_path.read_text())
        snapshot = load_configuration()
    with patch.object(ConfigurationManager.instance(), "_snapshot", snapshot):
        yield


class FeishuChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path_patch = patch(
            "src.channel.feishu.dedup.WORKSPACE_DEDUP_PATH",
            Path(directory.name) / "processed.jsonl",
        )
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def test_config_loads_feishu_env_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "FEISHU_APP_ID=cli_test\nFEISHU_APP_SECRET=secret_test\nFEISHU_LOG_LEVEL=DEBUG\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                with configured_environment(env_path):
                    config = load_feishu_config()

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
                with configured_environment(lark_env):
                    lark_config = load_feishu_config()
            with patch.dict(os.environ, {}, clear=True):
                with configured_environment(app_env):
                    app_config = load_feishu_config()

        self.assertEqual(lark_config.app_id, "cli_lark")
        self.assertEqual(lark_config.app_secret, "secret_lark")
        self.assertEqual(app_config.app_id, "cli_app")
        self.assertEqual(app_config.app_secret, "secret_app")

    def test_config_reports_missing_required_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError):
                    with configured_environment(env_path):
                        load_feishu_config()

    def test_message_deduplicator_filters_repeated_ids_and_enforces_lru(self) -> None:
        with patch("src.channel.feishu.dedup.MESSAGE_CACHE_CAPACITY", 2):
            dedup = MessageDeduplicator()

        self.assertTrue(admit(dedup, "msg_1"))
        self.assertFalse(admit(dedup, "msg_1"))
        self.assertTrue(admit(dedup, "msg_2"))
        self.assertTrue(admit(dedup, "msg_3"))
        self.assertTrue(admit(dedup, "msg_1"))

    def test_message_deduplicator_restores_persisted_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "processed.jsonl"
            path.write_text(json.dumps({"message_id": "msg_1", "chat_id": "chat_1"}) + "\n", encoding="utf-8")

            with patch("src.channel.feishu.dedup.WORKSPACE_DEDUP_PATH", path):
                dedup = MessageDeduplicator()

            self.assertFalse(admit(dedup, "msg_1"))
            self.assertTrue(admit(dedup, "msg_2"))

    def test_channel_marks_dedup_only_after_gateway_acceptance(self) -> None:
        received = []
        channel = FeishuChannel(FeishuConfig("app", "secret"), "feishu")
        channel.bind_buffer(SimpleNamespace(submit=lambda message: False))
        message = InboundMessage(
            id="msg-1", create_time=0, conversation=Conversation("chat", "p2p"),
            sender=Identity("person"), content=TextContent(text="hello"), content_text="hello",
        )
        with self.assertRaisesRegex(RuntimeError, "not accepted"):
            channel._deliver(message)
        channel.bind_buffer(SimpleNamespace(submit=lambda message: received.append(message) or True))
        channel._deliver(message)
        channel._deliver(message)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].sender_id, "person")
        message.id = "msg-2"
        message.conversation.chat_type = "group"
        channel._deliver(message)
        self.assertEqual(received[-1].session_id, "chat")

    def test_transport_modules_only_depend_on_runtime_message_contracts(self) -> None:
        feishu_dir = Path(__file__).resolve().parents[1] / "src" / "channel" / "feishu"
        forbidden = ("src.conductor.engine", "src.application.model", "src.tools", "src.prompts")
        transport_files = [
            path
            for path in feishu_dir.glob("*.py")
        ]

        for path in transport_files:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                modules = [
                    module
                    for node in ast.walk(tree)
                    for module in (
                        [node.module or ""] if isinstance(node, ast.ImportFrom)
                        else [alias.name for alias in node.names] if isinstance(node, ast.Import)
                        else []
                    )
                ]
                for module in modules:
                    self.assertFalse(any(module == item or module.startswith(item + ".") for item in forbidden))
                    if module == "src.agent_runtime" or module.startswith("src.agent_runtime."):
                        self.assertEqual(module, "src.channel.messages")


def admit(dedup, identity):
    if dedup.contains(identity):
        return False
    dedup.mark_processed(identity, "chat_1")
    return True


def test_sdk_delivery_uses_owner_loop_and_stops_sdk(monkeypatch):
    import asyncio
    import pytest
    import threading
    import time
    from lark_oapi.channel.types import SendResult
    from src.channel.feishu import channel as adapter
    from src.channel.messages import OutBoundMessage

    events = []
    ready = threading.Event()

    class SDK:
        def __init__(self, **kwargs):
            assert kwargs["safety"].dedup.enabled is False
            # Zero would silently drop every timestamped message in this SDK.
            assert kwargs["safety"].stale_message_window_ms > time.time() * 1000
            assert kwargs["policy"].require_mention is False

        def on(self, name, handler):
            assert name == "message"

        async def start_background(self, *, timeout):
            ready.set()

        async def send(self, to, message, opts):
            events.append((to, message.text, opts.get("reply_to"), threading.current_thread().name))
            if message.text == "failed":
                raise OSError("transient failure")
            return SendResult(success=True, message_id="sent")

        def stop(self):
            events.append("disconnected")

    monkeypatch.setattr(adapter, "FeishuSDK", SDK)
    channel = FeishuChannel(FeishuConfig("app", "secret"), "feishu")
    channel.bind_buffer(SimpleNamespace(submit=lambda _: True))
    async def run():
        await channel.start()
        assert ready.is_set()
        assert await channel.send_message(OutBoundMessage("feishu", "chat", "first"))
        with pytest.raises(OSError):
            await channel.send_message(OutBoundMessage("feishu", "chat", "failed"))
        assert await channel.send_message(OutBoundMessage("feishu", "chat", "last"))
        await channel.close()
    asyncio.run(run())
    assert events == [
        ("chat", "first", None, threading.current_thread().name),
        ("chat", "failed", None, threading.current_thread().name),
        ("chat", "last", None, threading.current_thread().name),
        "disconnected",
    ]


def test_inbound_preserves_sender_and_readable_mentions(tmp_path, monkeypatch):
    monkeypatch.setattr("src.channel.feishu.dedup.WORKSPACE_DEDUP_PATH", tmp_path / "messages.jsonl")
    from lark_oapi.channel.types import Mention
    received = []
    channel = FeishuChannel(FeishuConfig("app", "secret"), "feishu")
    channel.bind_buffer(SimpleNamespace(submit=lambda message: received.append(message) or True))
    channel._deliver(InboundMessage(
        id="sender-mention", create_time=0, conversation=Conversation("group", "group"),
        sender=Identity("person-id", display_name="A"),
        mentions=[Mention(key="@_user_1", name="robot")],
        content=TextContent(text="@_user_1 检查目录\n继续保留正文"),
    ))
    assert received[0].sender_id == "person-id"
    assert received[0].sender_name == "A"
    assert received[0].text == "@robot 检查目录\n继续保留正文"
