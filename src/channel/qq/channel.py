"""QQ text messages adapted to the common channel contract."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from src.channel.base import Channel
from src.channel.buffer import MessageBuffer
from src.channel.messages import InBoundMessage, OutBoundMessage

from .config import QQConfig
from .transport import QQTransport

if TYPE_CHECKING:
    from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message

    SDKMessage = C2CMessage | DirectMessage | GroupMessage | Message


@dataclass
class ReplyContext:
    kind: str
    target: str
    message_id: str
    received_at: float
    sequence: int = 0


class QQChannel(Channel):
    def __init__(self, config: QQConfig, channel_id: str = "qq") -> None:
        if not channel_id.strip():
            raise ValueError("Channel identity must be nonempty")
        self._config = config
        self._channel_id = channel_id
        self._buffer: MessageBuffer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._transport: QQTransport | None = None
        self._started = False
        self._closing = False
        self._replies: OrderedDict[str, ReplyContext] = OrderedDict()
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def bind_buffer(self, buffer: MessageBuffer) -> None:
        if self._started or self._closing:
            raise RuntimeError("Bind the channel buffer before startup")
        self._buffer = buffer

    async def start(self) -> None:
        if self._started or self._closing:
            raise RuntimeError("Channel cannot be started again")
        if self._buffer is None:
            raise RuntimeError("Channel has no message buffer")
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._transport = QQTransport(self._config, self._receive)
        try:
            await self._transport.start()
        except BaseException:
            await self.close()
            raise

    async def _receive(self, kind: str, message: SDKMessage) -> None:
        if self._closing or self._loop is None:
            return
        if asyncio.get_running_loop() is self._loop:
            self._deliver(kind, message)
        else:
            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(self._deliver_on_owner(kind, message), self._loop)
            )

    async def _deliver_on_owner(self, kind: str, message: SDKMessage) -> None:
        self._deliver(kind, message)

    def _deliver(self, kind: str, message: SDKMessage) -> None:
        if self._closing:
            return
        author = message.author
        text = (message.content or "").strip()
        if not text or getattr(author, "bot", False):
            return
        if kind == "group":
            target, sender = message.group_openid, author.member_openid
        elif kind == "c2c":
            target = sender = author.user_openid
        elif kind == "guild":
            target, sender = message.channel_id, author.id
        elif kind == "dm":
            target, sender = message.guild_id, author.id
        else:
            raise ValueError("Unknown QQ conversation kind")
        if not target or not sender or not message.id:
            return
        session_id = f"{kind}:{target}"
        identity = (session_id, message.id)
        if identity in self._seen:
            return
        incoming = InBoundMessage(
            self.channel_id, session_id, message.id, text, sender, getattr(author, "username", None)
        )
        if self._buffer is None or not self._buffer.submit(incoming):
            raise RuntimeError("Incoming message was not accepted by the agent inbox")
        self._seen[identity] = None
        if len(self._seen) > 4096:
            self._seen.popitem(last=False)
        self._replies[session_id] = ReplyContext(kind, target, message.id, monotonic())
        self._replies.move_to_end(session_id)
        if len(self._replies) > 4096:
            self._replies.popitem(last=False)

    async def send_message(self, message: OutBoundMessage) -> bool:
        if message.channel_id != self.channel_id:
            raise ValueError("Message belongs to a different channel")
        context = self._replies.get(message.session_id)
        if self._closing or self._transport is None or context is None or not message.text.strip():
            return False
        # Never silently convert an expired passive reply into a proactive send.
        if monotonic() - context.received_at >= 300:
            return False
        context.sequence += 1
        return await self._transport.send(
            context.kind, context.target, message.text, context.message_id, context.sequence
        )

    async def close(self) -> None:
        self._closing = True
        if self._transport is not None:
            await self._transport.close()
        self._buffer = None
        self._replies.clear()
        self._seen.clear()
