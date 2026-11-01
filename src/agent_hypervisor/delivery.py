"""Retry unacknowledged commands without repeating business task identities."""

from .routing import MailboxRouter


class DeliveryMonitor:
    def __init__(self, router: MailboxRouter) -> None:
        self._router = router

    async def run(self) -> None:
        while True:
            await self._router.wait_delivery_deadline()
            self._router.retry_expired()
