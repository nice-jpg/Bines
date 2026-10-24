"""One device lane per service, shared by tools, training and captcha setup."""

import asyncio
from collections.abc import Awaitable, Callable
from threading import Lock


class DeviceOperationExecutor:
    def __init__(self) -> None:
        self._lane: Lock = Lock()

    def execute[T](self, operation: Callable[[], T]) -> T:
        if not self._lane.acquire(blocking=False):
            raise DeviceOperationBusy("Another complete device operation is running.")
        try:
            return operation()
        finally:
            self._lane.release()

    async def aexecute[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        # Never block the event loop on a threading lock.
        if not self._lane.acquire(blocking=False):
            raise DeviceOperationBusy("Another complete device operation is running.")
        try:
            task: asyncio.Future[T] = asyncio.ensure_future(operation())
        except BaseException:
            self._lane.release()
            raise
        # Cancelling an async caller cannot stop an already running sync tool thread.
        # Keep its lane reserved until the actual operation finishes.
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _finished[T](self, task: asyncio.Future[T]) -> None:
        self._lane.release()
        if not task.cancelled():
            task.exception()  # Consume errors even when the awaiting caller was cancelled.


class DeviceOperationBusy(RuntimeError):
    """An independent device invocation is already in progress."""
