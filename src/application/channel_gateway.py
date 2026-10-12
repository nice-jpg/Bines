"""Public message interfaces backed by private, shutdown-aware queues."""

import logging
from collections.abc import Mapping
from functools import partial
from queue import Empty, Full, Queue, ShutDown
from threading import Thread
from typing import Final

from src.channel.base import Channel
from src.messaging.messages import IncomingMessage, MessageStreamClosed, OutgoingMessage

logger: logging.Logger = logging.getLogger(__name__)
INCOMING_CAPACITY: Final[int] = 1024
OUTGOING_CAPACITY: Final[int] = 256
SEND_TIMEOUT_SECONDS: Final[float] = 1.0


class ChannelGateway:
    """Expose queue-only capabilities; dispatch channels on the gateway thread."""

    def __init__(self, channels: Mapping[str, Channel]) -> None:
        self._channels: dict[str, Channel] = dict(channels)
        self._incoming: Queue[IncomingMessage] = Queue(maxsize=INCOMING_CAPACITY)
        self._outgoing: Queue[OutgoingMessage] = Queue(maxsize=OUTGOING_CAPACITY)
        self._thread: Thread = Thread(target=self._run, name="channel-gateway")
        for channel_id, channel in self._channels.items():
            channel.bind_message_listener(partial(self._receive, channel_id))

    def send_message(self, message: OutgoingMessage) -> bool:
        """Accept output, return False on congestion, or raise when closed."""
        try:
            self._outgoing.put(message, timeout=SEND_TIMEOUT_SECONDS)
        except Full:
            logger.error("Outgoing queue full for %s/%s", message.channel_id, message.conversation_id)
            return False
        except ShutDown as error:
            raise MessageStreamClosed("Outgoing message stream is closed") from error
        return True

    def receive_message(self, *, wait: bool) -> IncomingMessage | None:
        """Receive input; an empty open stream differs from a drained closed one."""
        try:
            return self._incoming.get(block=wait)
        except Empty:
            return None
        except ShutDown as error:
            raise MessageStreamClosed("Incoming message stream is closed and drained") from error

    def start(self) -> None:
        for channel_id, channel in self._channels.items():
            try:
                channel.start()
            except Exception:
                logger.exception("Channel startup failed: %s", channel_id)
        self._thread.start()

    def request_stop(self) -> None:
        """Stop input admission immediately, retaining all accepted input."""
        self._incoming.shutdown(immediate=False)

    def finish_output(self) -> None:
        """Close output after its producers finish; queued output still drains."""
        self._outgoing.shutdown(immediate=False)

    def join(self) -> None:
        self._thread.join()

    def _receive(self, channel_id: str, message: IncomingMessage) -> bool:
        if message.channel_id != channel_id or not message.text.strip():
            return False
        try:
            self._incoming.put_nowait(message)
        except (Full, ShutDown):
            return False
        return True

    def _run(self) -> None:
        try:
            while True:
                try:
                    message: OutgoingMessage = self._outgoing.get()
                except ShutDown:
                    break
                channel: Channel | None = self._channels.get(message.channel_id)
                if channel is None:
                    logger.error("No channel registered for %s", message.channel_id)
                    continue
                try:
                    channel.send_message(message)
                except Exception:
                    logger.exception("Channel rejected outgoing message: %s", message.channel_id)
        finally:
            self.request_stop()
            self.finish_output()
            for channel_id, channel in self._channels.items():
                try:
                    channel.close()
                except Exception:
                    logger.exception("Channel shutdown failed: %s", channel_id)
