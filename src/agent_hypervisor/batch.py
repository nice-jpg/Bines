"""Transfer claimed external batches into the root mailbox, once per batch."""

from src.channel.buffer import BufferClosed
from src.channel.gateway import Gateway

from .routing import MailboxRouter


class BatchCoordinator:
    def __init__(self, gateway: Gateway, router: MailboxRouter, root_id: str) -> None:
        self._gateway = gateway
        self._router = router
        self._root_id = root_id

    async def run(self) -> None:
        while True:
            try:
                batch = await self._gateway.take_batch()
            except BufferClosed:
                return
            try:
                while not self._router.deliver_batch(self._root_id, batch):
                    await self._router.wait_for_space(self._root_id)
                self._gateway.commit_batch(batch.batch_id)
            except BaseException:
                self._gateway.release_batch(batch.batch_id)
                raise
