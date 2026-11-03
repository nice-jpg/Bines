"""Offline QQ contract and SDK lifecycle regression tests."""

import asyncio
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.channel.buffer import MessageBuffer
from src.channel.messages import OutBoundMessage
from src.channel.qq import QQChannel, QQConfig, load_qq_config
from src.configuration.errors import ConfigurationError
from src.configuration.loader import parse_configuration


def message(identity="m1", text="hello"):
    return SimpleNamespace(
        id=identity,
        content=text,
        group_openid="same",
        channel_id="same",
        guild_id="same",
        author=SimpleNamespace(member_openid="u", user_openid="same", id="u", username="User", bot=False),
    )


def test_config(configuration_service):
    configuration_service._snapshot = parse_configuration(
        {
            "schema_version": 1,
            "channel": {
                "qq": {
                    "connections": {
                        "main": {
                            "app_id": "app",
                            "app_secret": "secret",
                            "sandbox": "true",
                            "direct_message": "false",
                        }
                    }
                }
            },
        }
    )
    config = load_qq_config("main")
    assert config.sandbox and not config.direct_message
    assert "secret" not in repr(config)
    with pytest.raises(ConfigurationError):
        QQConfig("", "secret")
    with pytest.raises(ConfigurationError):
        QQConfig("app", "secret", public_messages=False, public_guild_messages=False, direct_message=False)


def test_routing_dedup_sequence_and_expiry(monkeypatch):
    async def run():
        channel = QQChannel(QQConfig("app", "secret"))
        buffer = MessageBuffer()
        channel.bind_buffer(buffer)
        transport = SimpleNamespace(send=AsyncMock(return_value=True), close=AsyncMock())
        channel._transport = transport
        for kind in ("group", "c2c", "guild", "dm"):
            channel._deliver(kind, message())
            channel._deliver(kind, message())
            batch = await buffer.claim()
            assert batch.address.session_id == f"{kind}:same"
            assert len(batch.messages) == 1
            assert batch.messages[0].text == "hello"
            buffer.commit(batch.batch_id)
            outgoing = OutBoundMessage("qq", batch.address.session_id, "reply")
            assert await channel.send_message(outgoing)
            assert await channel.send_message(outgoing)
            assert transport.send.call_args.args == (kind, "same", "reply", "m1", 2)
        # Duplicate delivery cannot reset the reply counter or overwrite a newer context.
        channel._deliver("group", message("m2"))
        channel._deliver("group", message("m1"))
        assert await channel.send_message(OutBoundMessage("qq", "group:same", "new"))
        assert transport.send.call_args.args[-2:] == ("m2", 1)
        with pytest.raises(ValueError):
            await channel.send_message(OutBoundMessage("other", "group:same", "bad"))
        assert not await channel.send_message(OutBoundMessage("qq", "group:unknown", "bad"))
        monkeypatch.setattr("src.channel.qq.channel.monotonic", lambda: float("inf"))
        assert not await channel.send_message(OutBoundMessage("qq", "group:same", "late"))
        await channel.close()
        assert not await channel.send_message(OutBoundMessage("qq", "group:same", "closed"))

    asyncio.run(run())


def test_rejected_input_can_be_retried():
    channel = QQChannel(QQConfig("app", "secret"))
    channel.bind_buffer(SimpleNamespace(submit=lambda _: False))
    with pytest.raises(RuntimeError):
        channel._deliver("group", message())
    assert not channel._seen and not channel._replies
    accepted = []
    channel.bind_buffer(SimpleNamespace(submit=lambda msg: accepted.append(msg) or True))
    channel._deliver("group", message())
    channel._deliver("group", message("empty", "  "))
    assert len(accepted) == 1


def test_start_failure_cleanup(monkeypatch):
    transport = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("login failed")), close=AsyncMock())
    monkeypatch.setattr("src.channel.qq.channel.QQTransport", lambda *args: transport)

    async def run():
        channel = QQChannel(QQConfig("app", "secret"))
        with pytest.raises(RuntimeError, match="buffer"):
            await channel.start()
        channel.bind_buffer(MessageBuffer())
        with pytest.raises(RuntimeError, match="login failed"):
            await channel.start()
        transport.close.assert_awaited_once()
        with pytest.raises(RuntimeError, match="again"):
            await channel.start()

    asyncio.run(run())


def test_real_sdk_callbacks_api_routes_and_task_cleanup(monkeypatch):
    botpy = pytest.importorskip("botpy")
    from src.channel.qq.transport import QQTransport

    calls = []
    children = []

    async def idle():
        await asyncio.Event().wait()

    async def sdk_start(self, appid, secret):
        children.append(asyncio.create_task(idle()))
        await self.on_ready()
        for kind, callback in (
            ("group", "on_group_at_message_create"),
            ("c2c", "on_c2c_message_create"),
            ("guild", "on_at_message_create"),
            ("dm", "on_direct_message_create"),
        ):
            await getattr(self, callback)(message(kind))
        await asyncio.Event().wait()

    monkeypatch.setattr(botpy.Client, "start", sdk_start)
    for method in ("post_group_message", "post_c2c_message", "post_message", "post_dms"):

        async def send(self, _method=method, **kwargs):
            calls.append((_method, kwargs))
            return {"id": "sent"}

        monkeypatch.setattr(botpy.api.BotAPI, method, send)

    async def run():
        loop = asyncio.get_running_loop()
        handler = loop.get_exception_handler()
        owner = get_ident()
        channel = QQChannel(QQConfig("app", "secret"))
        buffer = MessageBuffer()
        channel.bind_buffer(buffer)
        await channel.start()
        assert isinstance(channel._transport, QQTransport)
        try:
            for kind in ("group", "c2c", "guild", "dm"):
                batch = await asyncio.wait_for(buffer.claim(), 3)
                assert get_ident() == owner
                assert batch.address.session_id == kind + ":same"
                assert await channel.send_message(OutBoundMessage("qq", batch.address.session_id, "response"))
                buffer.commit(batch.batch_id)
        finally:
            await channel.close()
        assert not channel._transport._thread.is_alive()
        assert all(task.cancelled() for task in children)
        assert loop.get_exception_handler() is handler
        assert [name for name, _ in calls] == [
            "post_group_message",
            "post_c2c_message",
            "post_message",
            "post_dms",
        ]
        for index, key in enumerate(("group_openid", "openid", "channel_id", "guild_id")):
            assert calls[index][1][key] == "same"
            assert calls[index][1]["content"] == "response"
        assert calls[0][1]["msg_seq"] == 1

    asyncio.run(run())
