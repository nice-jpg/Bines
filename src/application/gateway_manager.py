"""Process-wide gateway registry and routing-thread lifecycle."""

import logging
from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import ClassVar
from uuid import uuid4

from src.agent_runtime.messaging.messages import IncomingMessage, OutgoingMessage
from src.channel.base import Channel

from .channel_gateway import ChannelGateway

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class _GatewayRegistration:
    channel: Channel
    gateway: ChannelGateway
    thread: Thread | None = None
    joined: bool = False
    read_lock: Lock = field(default_factory=Lock)
    lifecycle_lock: Lock = field(default_factory=Lock)


class GatewayManager:
    _instance: ClassVar[GatewayManager | None] = None
    _instance_lock: ClassVar[Lock] = Lock()

    def __init__(self) -> None:
        raise RuntimeError("Use GatewayManager.instance()")

    @classmethod
    def instance(cls) -> GatewayManager:
        with cls._instance_lock:
            if cls._instance is None:
                manager: GatewayManager = object.__new__(cls)
                manager._registrations = {}
                manager._registry_lock = Lock()
                cls._instance = manager
            return cls._instance

    _registrations: dict[str, _GatewayRegistration]
    _registry_lock: Lock

    def create(self, channel: Channel) -> str:
        with self._registry_lock:
            if any(record.channel is channel for record in self._registrations.values()):
                raise ValueError("Channel is already registered")
            gateway_id: str = uuid4().hex
            self._registrations[gateway_id] = _GatewayRegistration(channel, ChannelGateway(channel))
            return gateway_id

    def _lookup(self, gateway_id: str) -> _GatewayRegistration:
        with self._registry_lock:
            record: _GatewayRegistration | None = self._registrations.get(gateway_id)
            if record is None:
                raise LookupError("No Gateway registered with this ID")
            return record

    def receive_messages(self, gateway_id: str, *, wait: bool) -> list[IncomingMessage]:
        record: _GatewayRegistration = self._lookup(gateway_id)
        if not record.read_lock.acquire(blocking=False):
            raise RuntimeError("Overlapping Gateway reads are not allowed")
        try:
            return record.gateway.receive_messages(wait=wait)
        finally:
            record.read_lock.release()

    def send_message(self, gateway_id: str, message: OutgoingMessage) -> bool:
        return self._lookup(gateway_id).gateway.send_message(message)

    def start(self, gateway_id: str) -> None:
        record: _GatewayRegistration = self._lookup(gateway_id)
        with record.lifecycle_lock:
            if record.thread is not None or record.joined:
                raise RuntimeError("Gateway already started or joined")
            record.thread = Thread(target=self._dispatch, args=(record,), name=f"gateway-{gateway_id}")
            try:
                record.channel.start()
                record.thread.start()
            except BaseException:
                record.gateway.stop()
                record.gateway.finish_output()
                record.channel.close()
                record.thread = None
                record.joined = True
                raise

    def _dispatch(self, record: _GatewayRegistration) -> None:
        try:
            record.gateway.dispatch()
        finally:
            record.gateway.stop()
            record.gateway.finish_output()
            try:
                record.channel.close()
            except Exception:
                logger.exception("Channel shutdown failed")

    def stop(self, gateway_id: str) -> None:
        self._lookup(gateway_id).gateway.stop()

    def finish_output(self, gateway_id: str) -> None:
        self._lookup(gateway_id).gateway.finish_output()

    def join(self, gateway_id: str) -> None:
        record: _GatewayRegistration = self._lookup(gateway_id)
        with record.lifecycle_lock:
            if record.joined:
                return
            if record.thread is not None:
                record.thread.join()
            else:
                record.gateway.stop()
                record.gateway.finish_output()
                record.channel.close()
            record.joined = True

    def release(self, gateway_id: str) -> None:
        record: _GatewayRegistration = self._lookup(gateway_id)
        with record.lifecycle_lock:
            if not record.joined:
                raise RuntimeError("Join Gateway before releasing it")
            with self._registry_lock:
                del self._registrations[gateway_id]
