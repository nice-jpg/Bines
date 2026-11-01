"""Feishu adapter with application-loop delivery and SDK-owned transport lifecycle."""

import asyncio
import json
import logging

from lark_oapi.channel import FeishuChannel as FeishuSDK  # pyright: ignore[reportMissingTypeStubs]
from lark_oapi.channel.config import (  # pyright: ignore[reportMissingTypeStubs]
    DedupConfig,
    InboundConfig,
    PolicyConfig,
    SafetyConfig,
    TextBatchConfig,
)
from lark_oapi.channel.types import (  # pyright: ignore[reportMissingTypeStubs]
    InboundMessage,
    OutboundText,
    SendResult,
    TextContent,
)
from lark_oapi.core.enum import LogLevel  # pyright: ignore[reportMissingTypeStubs]

from src.channel.base import Channel
from src.channel.buffer import MessageBuffer
from src.channel.messages import InBoundMessage, OutBoundMessage

from .config import FeishuConfig
from .dedup import MessageDeduplicator

logger: logging.Logger = logging.getLogger(__name__)


class FeishuChannel(Channel):
    """Marshal inbound callbacks onto the application loop, preserving chat identity."""

    def __init__(self, config: FeishuConfig, channel_id: str) -> None:
        self._config: FeishuConfig = config
        self._channel_id: str = channel_id
        self._buffer: MessageBuffer | None = None
        self._closing: bool = False
        self._started: bool = False
        self._deduplicator: MessageDeduplicator = MessageDeduplicator()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sdk: FeishuSDK | None = None

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def bind_buffer(self, buffer: MessageBuffer) -> None:
        if self._started:
            raise RuntimeError("Bind the channel buffer before startup")
        self._buffer = buffer

    async def start(self) -> None:
        if self._buffer is None:
            raise RuntimeError("Channel has no message buffer")
        if self._started or self._closing:
            raise RuntimeError("Channel cannot be started again")
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._sdk = self._build_sdk()
        self._sdk.on("message", self._receive)
        await self._sdk.start_background(timeout=30)

    async def send_message(self, message: OutBoundMessage) -> bool:
        if message.channel_id != self._channel_id:
            raise ValueError("Message belongs to a different channel")
        if self._sdk is None or self._closing:
            return False
        result: SendResult = await asyncio.wait_for(
            self._sdk.send(
                message.session_id, OutboundText(text=message.text), {"receive_id_type": "chat_id"}
            ),
            timeout=30,
        )
        return result.success

    async def close(self) -> None:
        self._closing = True
        if self._sdk is not None:
            await asyncio.to_thread(self._sdk.stop)
            self._sdk = None

    async def _receive(self, message: InboundMessage) -> None:
        loop = self._loop
        if loop is None or self._closing:
            return
        if asyncio.get_running_loop() is loop:
            self._deliver(message)
        else:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._deliver_on_owner(message), loop))

    async def _deliver_on_owner(self, message: InboundMessage) -> None:
        self._deliver(message)

    def _deliver(self, message: InboundMessage) -> None:
        if not isinstance(message.content, TextContent) or not message.content.text.strip():
            return
        text: str = message.content.text
        for mention in message.mentions:
            replacement: str = "@" + (
                mention.name or mention.open_id or mention.user_id or mention.key.lstrip("@")
            )
            text = text.replace(mention.key, replacement)
        incoming: InBoundMessage = InBoundMessage(
            self._channel_id,
            message.conversation.chat_id,
            message.id,
            text,
            message.sender.open_id,
            message.sender.display_name,
        )
        # Different configured accounts may use the same vendor message IDs.
        identity: str = (
            message.id if self._channel_id == "feishu" else json.dumps([self._channel_id, message.id])
        )
        if self._closing or self._deduplicator.contains(identity):
            return
        if self._buffer is None or not self._buffer.submit(incoming):
            raise RuntimeError("Incoming message was not accepted by the agent inbox")
        self._deduplicator.mark_processed(identity, incoming.session_id)

    def _build_sdk(self) -> FeishuSDK:
        return FeishuSDK(
            app_id=self._config.app_id,
            app_secret=self._config.app_secret,
            log_level=_log_level(self._config.log_level),
            policy=PolicyConfig(require_mention=False),
            inbound=InboundConfig(expand_merge_forward=False, fetch_interactive_card=False),
            safety=SafetyConfig(
                dedup=DedupConfig(enabled=False),
                text_batch=TextBatchConfig(delay_ms=0, long_delay_ms=0, max_messages=1),
                stale_message_window_ms=2**63 - 1,
            ),
        )


def _log_level(value: str) -> LogLevel:
    levels: dict[str, LogLevel] = {
        "CRITICAL": LogLevel.CRITICAL,
        "DEBUG": LogLevel.DEBUG,
        "ERROR": LogLevel.ERROR,
        "INFO": LogLevel.INFO,
        "WARNING": LogLevel.WARNING,
    }
    return levels.get(value.upper(), LogLevel.INFO)
