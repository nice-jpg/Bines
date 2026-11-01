"""Runtime-owned contracts; no channel or process-host dependencies."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

from src.common.utils.json import JsonObject

if TYPE_CHECKING:
    from .engine import AgentEngine
    from .mailbox import Mailbox


class MessageStreamClosed(Exception):
    """The owned mailbox no longer accepts or supplies messages."""


class TaskResult(BaseModel):
    status: Literal["progress", "blocked", "completed", "failed", "cancelled"]
    summary: str = Field(min_length=1)
    data: JsonObject = Field(default_factory=dict)


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    sequence: int
    sender_id: str
    target_id: str
    session_id: str | None
    kind: Literal["user", "task", "report", "notice"]
    text: str
    task_id: str = ""
    result: TaskResult | None = None
    channel_id: str | None = None
    sender_name: str | None = None
    batch_id: str | None = None
    origin: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, repr=False)
class MailboxToken:
    value: str = field(repr=False)


class MailboxPort(Protocol):
    def read(self, token: MailboxToken, *, wait: bool) -> list[AgentMessage]: ...
    async def receive(self, token: MailboxToken) -> list[AgentMessage]: ...
    async def acall(self, token: MailboxToken, child_id: str, task: str, origin: JsonObject) -> str: ...
    async def areport(self, token: MailboxToken, task_id: str, result: TaskResult) -> bool: ...
    def acknowledge(self, token: MailboxToken, message_ids: tuple[str, ...]) -> None: ...
    def call(self, token: MailboxToken, child_id: str, task: str, origin: JsonObject) -> str: ...
    def report(self, token: MailboxToken, task_id: str, result: TaskResult) -> bool: ...
    def unfinished(self, token: MailboxToken) -> list[str]: ...
    def tasks_blocked(self, token: MailboxToken) -> bool: ...
    def tasks_reported(self, token: MailboxToken) -> bool: ...
    def stop(self, token: MailboxToken) -> None: ...


class SpawnPort(Protocol):
    def child(self) -> AbstractContextManager[AgentHandle]: ...
    def start(self, engine: AgentEngine) -> None: ...
    def kill(self, child_id: str) -> None: ...


@dataclass(frozen=True)
class AgentHandle:
    agent_id: str
    mailbox: Mailbox
    spawner: SpawnPort
