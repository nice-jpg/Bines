"""Agent ownership, dedicated workers and parent-scoped delegation."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from threading import RLock
from typing import TYPE_CHECKING

from src.agent_hypervisor.routing import MailboxRouter
from src.agent_runtime.contracts import AgentHandle
from src.channel.base import Channel
from src.channel.gateway import Gateway

from .batch import BatchCoordinator
from .delivery import DeliveryMonitor

if TYPE_CHECKING:
    from src.agent_runtime.engine import AgentEngine
    from src.agent_runtime.worker import AgentWorker

    from .root import RootAgent


class AgentSpawner:
    """Parent-scoped lifetime capability; no access to host or other mailboxes."""

    def __init__(self, host: Host, parent_id: str) -> None:
        self._host = host
        self._parent_id = parent_id

    def child(self) -> AbstractContextManager[AgentHandle]:
        return self._host.child(self._parent_id)

    def start(self, engine: AgentEngine) -> None:
        if self._host.router.parent(engine.handle.agent_id) != self._parent_id:
            raise PermissionError("Can only start a direct child")
        self._host.start(engine)

    def kill(self, child_id: str) -> None:
        self._host.kill(self._parent_id, child_id)


class Host:
    """Own channel services and agent workers without a dedicated host thread."""

    def __init__(self) -> None:
        self.router = MailboxRouter()
        self._lock = RLock()
        self._workers: dict[str, AgentWorker] = {}
        self._identities: set[str] = set()
        self._closing = False
        self._root_id: str | None = None
        self._root: RootAgent | None = None
        self._gateway: Gateway | None = None
        self._services: list[asyncio.Task[None]] = []
        self._started_channels: list[Channel] = []

    def register_root(self) -> AgentHandle:
        with self._lock:
            self._ensure_open()
            identity = self.router.register_root()
            self._root_id = identity
            self._identities.add(identity)
            return self.handle(identity)

    def handle(self, identity: str) -> AgentHandle:
        return AgentHandle(identity, self.router.mailbox(identity), AgentSpawner(self, identity))

    @contextmanager
    def child(self, parent_id: str) -> Iterator[AgentHandle]:
        """Reserve a child identity and clean it up if construction or startup fails."""
        with self._lock:
            self._ensure_open()
            handle = self.handle(self.router.register_child(parent_id))
            self._identities.add(handle.agent_id)
        try:
            yield handle
            with self._lock:
                if handle.agent_id not in self._workers:
                    raise RuntimeError("Child construction must be followed by host.start(engine)")
        except BaseException:
            with self._lock:
                if handle.agent_id in self._identities:
                    self.router.stop(handle.agent_id)
                    if handle.agent_id not in self._workers:
                        self.router.release(handle.agent_id)
                        self._identities.remove(handle.agent_id)
            raise

    def start(self, engine: AgentEngine) -> None:
        """Start an explicitly constructed engine on its dedicated worker thread."""
        from src.agent_runtime.coordinator import AgentCoordinator
        from src.agent_runtime.worker import AgentWorker

        with self._lock:
            identity = engine.handle.agent_id
            if identity in self._workers:
                raise ValueError("Agent worker already started")
            try:
                self._ensure_open()
                if identity not in self._identities:
                    raise ValueError("Agent identity is not owned by this host")
                worker = AgentWorker(AgentCoordinator(engine))
                self.router.set_agent_type(identity, engine.agent_type)
                self._workers[identity] = worker
                worker.start()
            except BaseException:
                self._workers.pop(identity, None)
                engine.close()
                raise

    def kill(self, parent_id: str, child_id: str) -> None:
        with self._lock:
            if self.router.parent(child_id) != parent_id:
                raise PermissionError("Only the direct parent may kill an agent")
            identities = self._stop_tree(child_id)
            workers = [
                (identity, self._workers[identity]) for identity in identities if identity in self._workers
            ]
            for identity in identities:
                if identity not in self._workers:
                    self.router.release(identity)
                    self._identities.discard(identity)
        errors: list[Exception] = []
        for _, worker in workers:
            try:
                worker.join()
            except Exception as error:
                errors.append(error)
        with self._lock:
            for identity, _ in workers:
                if identity in self._workers:
                    self._workers.pop(identity, None)
                    self.router.release(identity)
                    self._identities.discard(identity)
        if errors:
            raise ExceptionGroup("Child workers failed during shutdown", errors)

    def _stop_tree(self, identity: str) -> list[str]:
        identities: list[str] = []
        for child in self.router.children(identity):
            identities.extend(self._stop_tree(child))
        self.router.stop(identity)
        return [*identities, identity]

    def stop(self) -> None:
        with self._lock:
            self._closing = True
            for identity in self._identities:
                self.router.stop(identity)

    async def open(self, root: RootAgent, channels: Sequence[Channel]) -> None:
        """Start the single root and communication services on the calling loop."""
        with self._lock:
            self._ensure_open()
            if self._root is not None:
                raise RuntimeError("Host services already started")
            if root.handle.agent_id != self._root_id:
                raise ValueError("Root agent is not registered with this host")
            self._root = root
        try:
            gateway = Gateway(channels)
            self._gateway = gateway
            root.bind_gateway(gateway)
            self.start(root)
            batches = BatchCoordinator(gateway, self.router, root.handle.agent_id)
            self._services.append(asyncio.create_task(batches.run(), name="channel-batches"))
            self._services.append(
                asyncio.create_task(DeliveryMonitor(self.router).run(), name="mail-delivery")
            )
            for channel in channels:
                channel.bind_buffer(gateway.buffer)
                self._started_channels.append(channel)
                await channel.start()
        except BaseException:
            await self.close()
            raise

    async def run(self, root: RootAgent, channels: Sequence[Channel]) -> None:
        """Run until the root or a required communication service exits."""
        await self.open(root, channels)
        lifetime = asyncio.create_task(self.wait_root(), name="root-lifetime")
        try:
            completed, _ = await asyncio.wait(
                [lifetime, *self._services], return_when=asyncio.FIRST_COMPLETED
            )
            for task in completed:
                task.result()
                if task is not lifetime:
                    raise RuntimeError("A communication service stopped unexpectedly")
        finally:
            lifetime.cancel()
            await asyncio.gather(lifetime, return_exceptions=True)
            cleanup = asyncio.create_task(self.close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def wait_root(self) -> None:
        with self._lock:
            if self._root_id is None or self._root_id not in self._workers:
                raise RuntimeError("Root worker has not been started")
            worker = self._workers[self._root_id]
        await worker.wait()

    async def close(self) -> None:
        self.stop()
        if self._gateway is not None:
            self._gateway.buffer.close()
        for task in self._services:
            task.cancel()
        await asyncio.gather(*self._services, return_exceptions=True)
        self._services.clear()
        with self._lock:
            workers = list(self._workers.items())
        errors = await asyncio.gather(*(worker.wait() for _, worker in workers), return_exceptions=True)
        with self._lock:
            for identity in self._identities:
                self.router.release(identity)
            self._identities.clear()
            self._workers.clear()
        failures = [error for error in errors if isinstance(error, Exception)]
        if self._root is not None:
            try:
                self._root.close()
            except Exception as error:
                failures.append(error)
        for channel in reversed(self._started_channels):
            try:
                await channel.close()
            except Exception as error:
                failures.append(error)
        self._started_channels.clear()
        if failures:
            raise ExceptionGroup("Host cleanup failed", failures)

    def _ensure_open(self) -> None:
        if self._closing:
            raise RuntimeError("Agent host is stopping")
