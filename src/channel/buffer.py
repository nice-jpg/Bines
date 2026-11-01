"""Owner-loop grouping and atomic batch claims for external input."""

import asyncio
from collections import OrderedDict
from itertools import count
from threading import get_ident
from uuid import uuid4

from .messages import ChannelBatch, InBoundMessage, SessionKey


class MessageBuffer:
    def __init__(self) -> None:
        self._thread = get_ident()
        self._groups: OrderedDict[SessionKey, tuple[int, list[InBoundMessage]]] = OrderedDict()
        self._released: list[ChannelBatch] = []
        self._claims: dict[str, ChannelBatch] = {}
        self._seen: set[tuple[SessionKey, str]] = set()
        self._sequence = count(1)
        self._readable = asyncio.Event()
        self._closed = False
        self._size = 0

    def _check_owner(self) -> None:
        if get_ident() != self._thread:
            raise RuntimeError("MessageBuffer must run on its owner thread")

    def submit(self, message: InBoundMessage) -> bool:
        self._check_owner()
        if self._closed or not message.text.strip() or not message.session_id:
            return False
        key = (message.address, message.message_id)
        if key in self._seen:
            return True
        if self._size >= 1024:
            return False
        if message.address not in self._groups:
            self._groups[message.address] = (next(self._sequence), [])
        self._groups[message.address][1].append(message)
        self._seen.add(key)
        self._size += 1
        self._readable.set()
        return True

    async def claim(self) -> ChannelBatch:
        self._check_owner()
        while True:
            if self._closed:
                raise BufferClosed("Channel input is closed")
            first = next(iter(self._groups.values()), None)
            if self._released and (first is None or self._released[0].first_sequence < first[0]):
                batch = self._released.pop(0)
            elif self._groups:
                address, (sequence, messages) = self._groups.popitem(last=False)
                batch = ChannelBatch(uuid4().hex, address, sequence, tuple(messages))
            else:
                self._readable.clear()
                await self._readable.wait()
                continue
            self._claims[batch.batch_id] = batch
            return batch

    def commit(self, batch_id: str) -> None:
        self._check_owner()
        batch = self._claims.pop(batch_id)
        self._size -= len(batch.messages)

    def release(self, batch_id: str) -> None:
        self._check_owner()
        self._released.append(self._claims.pop(batch_id))
        self._released.sort(key=lambda batch: batch.first_sequence)
        self._readable.set()

    def close(self) -> None:
        self._check_owner()
        self._closed = True
        self._readable.set()


class BufferClosed(Exception):
    """No more input can be claimed."""
