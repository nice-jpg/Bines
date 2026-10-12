"""Feishu SDK adapter; transport lifecycle stays inside the channel worker."""

import asyncio
import json
import logging
from queue import Empty, Queue
from threading import RLock, Thread

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

from src.channel.base import Channel, MessageListener
from src.messaging.messages import IncomingMessage, OutgoingMessage

from .config import FeishuConfig
from .dedup import MessageDeduplicator

logger: logging.Logger = logging.getLogger(__name__)


class FeishuChannel(Channel):
    """Translate Feishu messages and serialize delivery on an owned worker."""

    def __init__(self, config: FeishuConfig, channel_id: str) -> None:
        self._config: FeishuConfig = config
        self._channel_id: str = channel_id
        self._listener: MessageListener | None = None
        self._outgoing: Queue[OutgoingMessage] = Queue(maxsize=256)
        self._lock: RLock = RLock()
        self._closing: bool = False
        self._started: bool = False
        self._deduplicator: MessageDeduplicator = MessageDeduplicator()
        self._thread: Thread = Thread(target=self._run, name=f"channel-{channel_id}")

    def bind_message_listener(self, listener: MessageListener) -> None:
        if self._started:
            raise RuntimeError("Bind the channel listener before startup")
        self._listener = listener

    def start(self) -> None:
        if self._listener is None:
            raise RuntimeError("Channel has no message listener")
        with self._lock:
            if self._started or self._closing:
                raise RuntimeError("Channel cannot be started again")
            self._started = True
        self._thread.start()

    def send_message(self, message: OutgoingMessage) -> None:
        if message.channel_id != self._channel_id:
            raise ValueError("Message belongs to a different channel")
        with self._lock:
            if not self._started or self._closing or not self._thread.is_alive():
                raise RuntimeError("Channel is not running")
            self._outgoing.put_nowait(message)

    def close(self) -> None:
        with self._lock:
            self._closing = True
        if self._started:
            self._thread.join()

    def _receive(self, message: InboundMessage) -> None:
        if not isinstance(message.content, TextContent) or not message.content.text.strip():
            return
        incoming: IncomingMessage = IncomingMessage(
            self._channel_id,
            message.conversation.chat_id,
            message.id,
            message.content.text.strip(),
            None if message.conversation.chat_type == "p2p" else message.id,
        )
        # Different configured accounts may use the same vendor message IDs.
        identity: str = (
            message.id if self._channel_id == "feishu" else json.dumps([self._channel_id, message.id])
        )
        with self._lock:
            if self._closing or self._deduplicator.contains(identity):
                return
            listener: MessageListener | None = self._listener
            if listener is None or not listener(incoming):
                raise RuntimeError("Incoming message was not accepted by the gateway")
            self._deduplicator.mark_processed(identity, incoming.conversation_id)

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:
            logger.exception("Feishu channel stopped: %s", self._channel_id)
        finally:
            with self._lock:
                self._closing = True
            while True:
                try:
                    message: OutgoingMessage = self._outgoing.get_nowait()
                except Empty:
                    break
                logger.error("Undelivered Feishu output for %s/%s", self._channel_id, message.conversation_id)

    async def _serve(self) -> None:
        sdk: FeishuSDK = FeishuSDK(
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
        sdk.on("message", self._receive)
        try:
            await sdk.start_background(timeout=30)
            while True:
                try:
                    message: OutgoingMessage = self._outgoing.get_nowait()
                except Empty:
                    with self._lock:
                        if self._closing:
                            break
                    await asyncio.sleep(0.02)
                    continue
                try:
                    result: SendResult = await asyncio.wait_for(
                        sdk.send(  # pyright: ignore[reportUnknownMemberType]
                            message.conversation_id,
                            OutboundText(text=message.text),
                            {"receive_id_type": "chat_id", "reply_to": message.reply_to},
                        ),
                        timeout=30,
                    )
                    if not result.success:
                        logger.error("Feishu delivery failed: %s", result.error)
                except Exception:
                    logger.exception("Feishu delivery failed for %s", message.conversation_id)
        finally:
            await sdk.disconnect()


def _log_level(value: str) -> LogLevel:
    levels: dict[str, LogLevel] = {
        "CRITICAL": LogLevel.CRITICAL,
        "DEBUG": LogLevel.DEBUG,
        "ERROR": LogLevel.ERROR,
        "INFO": LogLevel.INFO,
        "WARNING": LogLevel.WARNING,
    }
    return levels.get(value.upper(), LogLevel.INFO)
