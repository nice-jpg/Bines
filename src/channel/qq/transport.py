"""Isolate botpy's loop policy and child tasks from the application's loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from threading import Event, Thread
from typing import TYPE_CHECKING, Any

from .config import QQConfig

if TYPE_CHECKING:
    from botpy.manage import GroupManageEvent
    from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
    SDKMessage = C2CMessage | DirectMessage | GroupMessage | Message


class QQTransport:
    def __init__(self, config: QQConfig, receive: Callable[[str, SDKMessage], Awaitable[None]]) -> None:
        self._config = config
        self._receive = receive
        self._ready: Future[None] = Future()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._client: Any = None
        self._thread: Thread | None = None
        self._finished = False
        self._closing = Event()

    async def start(self) -> None:
        self._thread = Thread(target=self._run, name="qq-botpy", daemon=True)
        self._thread.start()
        try:
            await asyncio.wait_for(asyncio.wrap_future(self._ready), 30)
        except BaseException:
            await self.close()
            raise

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)
            elif not self._closing.is_set():
                logging.getLogger(__name__).exception("QQ transport failed")
        finally:
            self._finished = True

    async def _serve(self) -> None:
        # Optional dependency: importing the channel itself does not require botpy.
        import botpy

        transport = self

        class Client(botpy.Client):
            async def on_ready(self) -> None:
                if not transport._ready.done():
                    transport._ready.set_result(None)

            async def on_group_at_message_create(self, message: GroupMessage) -> None:
                await transport._receive("group", message)

            async def on_c2c_message_create(self, message: C2CMessage) -> None:
                await transport._receive("c2c", message)

            async def on_at_message_create(self, message: Message) -> None:
                await transport._receive("guild", message)

            async def on_direct_message_create(self, message: DirectMessage) -> None:
                await transport._receive("dm", message)

            async def on_group_message_create(self, message: GroupMessage) -> None:
                await transport._receive("group", message)

        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        if self._closing.is_set():
            return
        config = self._config
        self._client = Client(
            intents=botpy.Intents(
                guild_messages=True,
                public_messages=True,
                public_guild_messages=config.public_guild_messages,
                direct_message=config.direct_message,
            ),
            is_sandbox=config.sandbox,
            ext_handlers=False,
        )
        async with self._client:
            running = asyncio.create_task(self._client.start(appid=config.app_id, secret=config.app_secret))
            stopping = asyncio.create_task(self._stop.wait())
            try:
                done, _ = await asyncio.wait((running, stopping), return_when=asyncio.FIRST_COMPLETED)
                if running in done:
                    await running
                    raise RuntimeError("QQ transport stopped unexpectedly")
            finally:
                running.cancel()
                stopping.cancel()
                await asyncio.gather(running, stopping, return_exceptions=True)
        # asyncio.run cancels and awaits botpy's connection, heartbeat and callback
        # tasks on this dedicated loop, never unrelated application tasks.

    async def send(self, kind: str, target: str, text: str, msg_id: str, sequence: int) -> bool:
        loop = self._loop
        if self._finished or loop is None or self._stop is None or self._stop.is_set():
            return False

        async def deliver() -> bool:
            api = self._client.api
            common = {"content": text, "msg_id": msg_id}
            if kind == "group":
                result = await api.post_group_message(
                    group_openid=target, msg_type=0, msg_seq=sequence, **common
                )
            elif kind == "c2c":
                result = await api.post_c2c_message(openid=target, msg_type=0, msg_seq=sequence, **common)
            elif kind == "guild":
                result = await api.post_message(channel_id=target, **common)
            else:
                result = await api.post_dms(guild_id=target, **common)
            return bool(result and result.get("id"))

        return await asyncio.wait_for(
            asyncio.wrap_future(asyncio.run_coroutine_threadsafe(deliver(), loop)), 30
        )

    async def close(self) -> None:
        self._closing.set()
        if self._loop is not None and self._stop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                # The SDK thread may have finished between the check and scheduling.
                if not self._loop.is_closed():
                    raise
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join)
