"""External batch delivery and addressed output, independent of agents."""

import asyncio
from collections.abc import Sequence

from .base import Channel
from .buffer import MessageBuffer
from .messages import ChannelBatch, OutBoundMessage, SessionKey


class Gateway:
    def __init__(self, channels: Sequence[Channel]) -> None:
        self.buffer = MessageBuffer()
        self._channels = {channel.channel_id: channel for channel in channels}
        if not self._channels or len(self._channels) != len(channels):
            raise ValueError("Channels must be nonempty and have unique identities")
        self._addresses: set[SessionKey] = set()

    async def take_batch(self) -> ChannelBatch:
        batch = await self.buffer.claim()
        self._addresses.add(batch.address)
        return batch

    def commit_batch(self, batch_id: str) -> None:
        self.buffer.commit(batch_id)

    def release_batch(self, batch_id: str) -> None:
        self.buffer.release(batch_id)

    async def send(self, message: OutBoundMessage) -> bool:
        address = SessionKey(message.channel_id, message.session_id)
        if address not in self._addresses:
            raise PermissionError("Unknown channel/session address")
        return await asyncio.wait_for(self._channels[message.channel_id].send_message(message), 30)
