"""One channel's private message queues, independent of instance/thread management."""

import logging
from queue import Empty, Full, Queue, ShutDown
from threading import Lock
from typing import Final

from src.agent_runtime.messaging.messages import IncomingMessage, MessageStreamClosed, OutgoingMessage
from src.channel.base import Channel

logger: logging.Logger = logging.getLogger(__name__)
INCOMING_CAPACITY: Final[int] = 1024
OUTGOING_CAPACITY: Final[int] = 256
SEND_TIMEOUT_SECONDS: Final[float] = 1.0


class ChannelGateway:
    def __init__(self, channel: Channel) -> None:
        self._channel: Channel = channel
        self._incoming: Queue[IncomingMessage] = Queue(maxsize=INCOMING_CAPACITY)
        self._outgoing: Queue[OutgoingMessage] = Queue(maxsize=OUTGOING_CAPACITY)
        self._admission_lock: Lock = Lock()
        channel.bind_message_listener(self._receive)

    def receive_messages(self, *, wait: bool) -> list[IncomingMessage]:
        try:
            first: IncomingMessage = self._incoming.get(block=wait)
        except Empty:
            return []
        except ShutDown as error:
            raise MessageStreamClosed("Incoming message stream is closed and drained") from error
        batch: list[IncomingMessage] = [first]
        # Producers cannot extend this snapshot. Manager serializes consumers.
        with self._admission_lock:
            for _ in range(self._incoming.qsize()):
                batch.append(self._incoming.get_nowait())
        return batch

    def send_message(self, message: OutgoingMessage) -> bool:
        if message.channel_id != self._channel.channel_id:
            raise ValueError("Message belongs to a different channel")
        try:
            self._outgoing.put(message, timeout=SEND_TIMEOUT_SECONDS)
        except Full:
            logger.error("Outgoing queue full for %s/%s", message.channel_id, message.conversation_id)
            return False
        except ShutDown as error:
            raise MessageStreamClosed("Outgoing message stream is closed") from error
        return True

    def stop(self) -> None:
        self._incoming.shutdown(immediate=False)

    def finish_output(self) -> None:
        self._outgoing.shutdown(immediate=False)

    def _receive(self, message: IncomingMessage) -> bool:
        if message.channel_id != self._channel.channel_id or not message.text.strip():
            return False
        with self._admission_lock:
            try:
                self._incoming.put_nowait(message)
            except (Full, ShutDown):
                return False
        return True

    def dispatch(self) -> None:
        while True:
            try:
                message: OutgoingMessage = self._outgoing.get()
            except ShutDown:
                return
            try:
                self._channel.send_message(message)
            except Exception:
                logger.exception("Channel rejected outgoing message: %s", message.channel_id)
