"""A single agent's authenticated mailbox; it never exposes the router registry."""

from src.common.utils.json import JsonObject

from .contracts import AgentMessage, MailboxPort, MailboxToken, TaskResult


class Mailbox:
    def __init__(self, token: MailboxToken, port: MailboxPort) -> None:
        self._token = token
        self._port = port

    def read(self, *, wait: bool) -> list[AgentMessage]:
        return self._port.read(self._token, wait=wait)

    async def receive(self) -> list[AgentMessage]:
        return await self._port.receive(self._token)

    async def areceive_nowait(self) -> list[AgentMessage]:
        return self._port.read(self._token, wait=False)

    def acknowledge(self, messages: list[AgentMessage]) -> None:
        self._port.acknowledge(self._token, tuple(message.message_id for message in messages))

    def call(self, child_id: str, task: str, origin: JsonObject | None = None) -> str:
        return self._port.call(self._token, child_id, task, origin or {})

    async def acall(self, child_id: str, task: str) -> str:
        return await self._port.acall(self._token, child_id, task, {})

    def report(self, task_id: str, result: TaskResult) -> bool:
        return self._port.report(self._token, task_id, result)

    async def areport(self, task_id: str, result: TaskResult) -> bool:
        return await self._port.areport(self._token, task_id, result)

    def unfinished(self) -> list[str]:
        return self._port.unfinished(self._token)

    def tasks_blocked(self) -> bool:
        return self._port.tasks_blocked(self._token)

    def tasks_reported(self) -> bool:
        return self._port.tasks_reported(self._token)

    def stop(self) -> None:
        self._port.stop(self._token)
