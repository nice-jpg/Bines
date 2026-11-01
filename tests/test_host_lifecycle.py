import asyncio

import pytest
from test_agent_router import EchoModel
from test_channel_workers import ChannelStub

from src.agent_hypervisor.delivery import DeliveryMonitor
from src.agent_hypervisor.host import Host
from src.agent_hypervisor.root import RootAgent


def test_wait_root_requires_a_started_worker() -> None:
    async def run() -> None:
        host = Host()
        with pytest.raises(RuntimeError, match="Root worker has not been started"):
            await host.wait_root()
        host.register_root()
        with pytest.raises(RuntimeError, match="Root worker has not been started"):
            await host.wait_root()
        await host.close()

    asyncio.run(run())


def test_root_exit_closes_host_services_and_channels() -> None:
    async def run() -> None:
        class StoppingChannel(ChannelStub):
            async def start(self) -> None:
                await super().start()
                root.handle.mailbox.stop()

        host = Host()
        root = RootAgent(model=EchoModel(), handle=host.register_root())
        channel = StoppingChannel()
        await asyncio.wait_for(host.run(root, [channel]), 3)
        assert channel.closed
        assert not host._workers
        assert not host._services
        assert not host._identities
        await host.close()

    asyncio.run(run())


def test_service_failure_closes_host(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(self: DeliveryMonitor) -> None:
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(DeliveryMonitor, "run", fail)

    async def run() -> None:
        host = Host()
        root = RootAgent(model=EchoModel(), handle=host.register_root())
        channel = ChannelStub()
        with pytest.raises(RuntimeError, match="delivery failed"):
            await asyncio.wait_for(host.run(root, [channel]), 3)
        assert channel.closed
        assert not host._workers
        assert not host._services

    asyncio.run(run())


def test_invalid_channels_clean_up_registered_root() -> None:
    async def run() -> None:
        host = Host()
        root = RootAgent(model=EchoModel(), handle=host.register_root())
        channel = ChannelStub()
        with pytest.raises(ValueError, match="unique identities"):
            await host.open(root, [channel, channel])
        assert not host._workers
        assert not host._identities
        with pytest.raises(RuntimeError, match="closed"):
            await root.arun_turn()

    asyncio.run(run())
