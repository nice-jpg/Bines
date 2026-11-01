"""Thread-safe, parent-child mailboxes with explicit task correlation."""

import asyncio
import json
from concurrent.futures import Future, InvalidStateError
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import count
from queue import Empty, Full, Queue, ShutDown
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

from src.agent_runtime.contracts import AgentMessage, MailboxToken, MessageStreamClosed, TaskResult
from src.agent_runtime.mailbox import Mailbox
from src.channel.messages import ChannelBatch, InBoundMessage
from src.common.utils.json import JsonObject


@dataclass
class _Mailbox:
    parent_id: str | None
    queue: Queue[AgentMessage] = field(default_factory=lambda: Queue(maxsize=1024))
    consumer: Lock = field(default_factory=Lock)
    closed: bool = False
    conversations: set[str] = field(default_factory=set)
    waiters: set[Future[None]] = field(default_factory=set)
    space_waiters: set[Future[None]] = field(default_factory=set)
    queued: set[str] = field(default_factory=set)
    received: dict[str, AgentMessage] = field(default_factory=dict)
    acknowledged: set[str] = field(default_factory=set)


@dataclass
class _Task:
    caller: str
    callee: str
    completed: bool = False
    delivered: bool = False
    blocked: bool = False
    command: AgentMessage | None = None
    deadline: float = 0
    attempts: int = 0
    delivery_unknown: bool = False
    terminal: TaskResult | None = None
    origin: JsonObject = field(default_factory=dict)


class MailboxRouter:
    """Route envelopes, never execute agents or call channel transports."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._agents: dict[str, _Mailbox] = {}
        self._root_id: str | None = None
        self._tasks: dict[str, _Task] = {}
        self._sequence = count(1)
        self._tokens: dict[MailboxToken, str] = {}
        self._batches: set[str] = set()
        self._delivery_waiters: set[Future[None]] = set()

    def deliver_batch(self, root_id: str, batch: ChannelBatch) -> bool:
        with self._lock:
            root = self._open(root_id)
            if root.parent_id is not None:
                raise PermissionError("Only a root accepts external batches")
            if batch.batch_id in self._batches:
                return True
            payload = json.dumps(
                {
                    "batch_id": batch.batch_id,
                    "channel_id": batch.address.channel_id,
                    "session_id": batch.address.session_id,
                    "messages": [
                        {
                            "message_id": message.message_id,
                            "sender_id": message.sender_id,
                            "text": message.text,
                        }
                        for message in batch.messages
                    ],
                },
                ensure_ascii=False,
            )
            envelope = AgentMessage(
                batch.batch_id,
                next(self._sequence),
                "channel",
                root_id,
                batch.address.session_id,
                "user",
                payload,
                channel_id=batch.address.channel_id,
                batch_id=batch.batch_id,
            )
            if not self._put(root, envelope):
                return False
            self._batches.add(batch.batch_id)
            return True

    def mailbox(self, agent_id: str) -> Mailbox:
        """Issue an authenticated capability to the host, never to model input."""
        with self._lock:
            self._open(agent_id)
            token = next((token for token, owner in self._tokens.items() if owner == agent_id), None)
            if token is None:
                token = MailboxToken(uuid4().hex)
            self._tokens[token] = agent_id
            return Mailbox(token, AuthenticatedMailboxPort(self))

    def authenticate(self, token: MailboxToken) -> str:
        with self._lock:
            identity = self._tokens.get(token)
            if identity is None:
                raise PermissionError("Invalid mailbox credential")
            self._open(identity)
            return identity

    async def receive(self, agent_id: str) -> list[AgentMessage]:
        while True:
            with self._lock:
                mailbox = self._open(agent_id)
                if not mailbox.queue.empty():
                    return self.read(agent_id, wait=False)
                waiter: Future[None] = Future()
                mailbox.waiters.add(waiter)
            try:
                await asyncio.wrap_future(waiter)
            finally:
                with self._lock:
                    mailbox.waiters.discard(waiter)

    def acknowledge(self, agent_id: str, message_ids: tuple[str, ...]) -> None:
        with self._lock:
            mailbox = self._open(agent_id)
            for message_id in message_ids:
                if message_id in mailbox.acknowledged:
                    continue
                message = mailbox.received.pop(message_id, None)
                if message is None:
                    raise ValueError("Cannot acknowledge an unreceived message")
                mailbox.acknowledged.add(message_id)
                if message.kind == "task":
                    task = self._tasks[message.task_id]
                    task.delivered = True
                    task.delivery_unknown = False
                    task.command = None

    def retry_expired(self) -> None:
        with self._lock:
            now = monotonic()
            for task_id, task in self._tasks.items():
                if task.delivered or task.completed or task.delivery_unknown or task.deadline > now:
                    continue
                if task.attempts < 3:
                    if task.command is not None:
                        self._put(self._open(task.callee), task.command)
                    task.attempts += 1
                    task.deadline = now + 30
                    continue
                notice = AgentMessage(
                    f"delivery-unknown:{task_id}",
                    next(self._sequence),
                    task.callee,
                    task.caller,
                    None,
                    "notice",
                    "Task delivery was not acknowledged; execution status is unknown.",
                    task_id,
                )
                if self._put(self._open(task.caller), notice):
                    task.delivery_unknown = True

    async def wait_delivery_deadline(self) -> None:
        with self._lock:
            deadlines = [
                task.deadline
                for task in self._tasks.values()
                if not task.delivered and not task.completed and not task.delivery_unknown
            ]
            timeout = max(0.01, min(deadlines) - monotonic()) if deadlines else None
            waiter: Future[None] = Future()
            self._delivery_waiters.add(waiter)
        try:
            await asyncio.wait_for(asyncio.wrap_future(waiter), timeout)
        except TimeoutError:
            pass
        finally:
            with self._lock:
                self._delivery_waiters.discard(waiter)

    async def wait_for_space(self, agent_id: str) -> None:
        with self._lock:
            mailbox = self._open(agent_id)
            if not mailbox.queue.full():
                return
            waiter: Future[None] = Future()
            mailbox.space_waiters.add(waiter)
        try:
            await asyncio.wrap_future(waiter)
        finally:
            with self._lock:
                mailbox.space_waiters.discard(waiter)

    def register_root(self) -> str:
        with self._lock:
            if self._root_id is not None:
                raise ValueError("A host has exactly one root agent")
            identity = uuid4().hex
            self._agents[identity] = _Mailbox(None)
            self._root_id = identity
            return identity

    def register_child(self, parent_id: str) -> str:
        with self._lock:
            self._open(parent_id)
            identity = uuid4().hex
            self._agents[identity] = _Mailbox(parent_id)
            return identity

    def parent(self, agent_id: str) -> str | None:
        with self._lock:
            return self._agents[agent_id].parent_id

    def deliver_user(self, message: InBoundMessage) -> bool:
        with self._lock:
            identity = self._root_id
            if identity is None or not message.session_id or not message.text.strip():
                return False
            root = self._agents[identity]
            envelope = AgentMessage(
                f"{message.channel_id}:{message.session_id}:{message.message_id}",
                next(self._sequence),
                message.sender_id,
                identity,
                message.session_id,
                "user",
                message.text,
                channel_id=message.channel_id,
                sender_name=message.sender_name,
            )
            if not self._put(root, envelope):
                return False
            root.conversations.add(message.session_id)
            return True

    def call(self, sender_id: str, child_id: str, task: str, origin: JsonObject | None = None) -> str:
        if not task.strip():
            raise ValueError("Task must not be empty")
        with self._lock:
            self._open(sender_id)
            child = self._open(child_id)
            if child.parent_id != sender_id:
                raise PermissionError("Tasks may only be sent to a direct child")
            task_id = uuid4().hex
            message = AgentMessage(
                uuid4().hex,
                next(self._sequence),
                sender_id,
                child_id,
                None,
                "task",
                task,
                task_id,
            )
            if not self._put(child, message):
                raise MessageStreamClosed("Child inbox is full or closed")
            self._tasks[task_id] = _Task(
                sender_id, child_id, command=message, deadline=monotonic() + 30, origin=deepcopy(origin or {})
            )
            self._notify(self._delivery_waiters)
            return task_id

    def report(self, sender_id: str, task_id: str, result: TaskResult) -> bool:
        with self._lock:
            self._open(sender_id)
            task = self._tasks.get(task_id)
            if task is None or task.callee != sender_id or not task.delivered:
                raise PermissionError("Only the recipient of a delivered task may report it")
            if task.completed:
                if task.terminal == result:
                    return True
                raise ValueError("Conflicting result for completed task")
            parent = self._open(task.caller)
            message = AgentMessage(
                uuid4().hex,
                next(self._sequence),
                sender_id,
                task.caller,
                None,
                "report",
                result.summary,
                task_id,
                result.model_copy(deep=True),
                origin=deepcopy(task.origin),
            )
            if not self._put(parent, message):
                return False
            task.completed = result.status in {"completed", "failed", "cancelled"}
            if task.completed:
                task.terminal = result.model_copy(deep=True)
            task.blocked = result.status == "blocked"
            return True

    def tasks_blocked(self, agent_id: str) -> bool:
        """Whether all delivered, unfinished tasks explicitly report being blocked."""
        with self._lock:
            tasks = [
                task
                for task in self._tasks.values()
                if task.callee == agent_id and task.delivered and not task.completed
            ]
            return bool(tasks) and all(task.blocked for task in tasks)

    def tasks_reported(self, agent_id: str) -> bool:
        with self._lock:
            tasks = [task for task in self._tasks.values() if task.callee == agent_id and task.delivered]
            return bool(tasks) and all(task.completed for task in tasks)

    def unfinished(self, agent_id: str) -> list[str]:
        with self._lock:
            return [
                key
                for key, task in self._tasks.items()
                if task.callee == agent_id and task.delivered and not task.completed
            ]

    def children(self, agent_id: str) -> list[str]:
        with self._lock:
            return [key for key, value in self._agents.items() if value.parent_id == agent_id]

    def read(self, agent_id: str, *, wait: bool) -> list[AgentMessage]:
        with self._lock:
            mailbox = self._agents[agent_id]
        if not mailbox.consumer.acquire(blocking=False):
            raise RuntimeError("Overlapping reads of one agent inbox are forbidden")
        try:
            try:
                first = mailbox.queue.get(block=wait)
            except Empty:
                return []
            except ShutDown as error:
                raise MessageStreamClosed("Agent inbox is closed") from error
            batch = [first]
            with self._lock:
                for _ in range(mailbox.queue.qsize()):
                    batch.append(mailbox.queue.get_nowait())
                for message in batch:
                    mailbox.queued.discard(message.message_id)
                    if message.message_id not in mailbox.acknowledged:
                        mailbox.received[message.message_id] = message
                self._notify(mailbox.space_waiters)
            return [message for message in batch if message.message_id not in mailbox.acknowledged]
        finally:
            mailbox.consumer.release()

    def stop(self, agent_id: str) -> None:
        with self._lock:
            mailbox = self._agents[agent_id]
            if mailbox.closed:
                return
            for task_id, task in self._tasks.items():
                if task.callee == agent_id and not task.completed:
                    parent = self._agents.get(task.caller)
                    if parent is not None:
                        result = TaskResult(status="failed", summary="Child agent was stopped.")
                        self._put(
                            parent,
                            AgentMessage(
                                uuid4().hex,
                                next(self._sequence),
                                agent_id,
                                task.caller,
                                None,
                                "report",
                                result.summary,
                                task_id,
                                result,
                            ),
                        )
                    task.completed = True
            mailbox.closed = True
            mailbox.queue.shutdown(immediate=True)
            self._wake(mailbox)
            self._notify(mailbox.space_waiters)

    def release(self, agent_id: str) -> None:
        with self._lock:
            self._agents.pop(agent_id)
            self._tokens = {token: owner for token, owner in self._tokens.items() if owner != agent_id}
            if self._root_id == agent_id:
                self._root_id = None
            self._tasks = {
                key: task for key, task in self._tasks.items() if agent_id not in (task.caller, task.callee)
            }

    def _open(self, agent_id: str) -> _Mailbox:
        mailbox = self._agents.get(agent_id)
        if mailbox is None or mailbox.closed:
            raise MessageStreamClosed("Agent is not registered or is stopping")
        return mailbox

    @staticmethod
    def _wake(mailbox: _Mailbox) -> None:
        MailboxRouter._notify(mailbox.waiters)

    @staticmethod
    def _notify(waiters: set[Future[None]]) -> None:
        for waiter in tuple(waiters):
            try:
                waiter.set_result(None)
            except InvalidStateError:
                pass  # A cancelled cross-loop reader no longer needs a wakeup.

    @staticmethod
    def _put(mailbox: _Mailbox, message: AgentMessage) -> bool:
        if mailbox.closed:
            return False
        if message.message_id in mailbox.queued or message.message_id in mailbox.acknowledged:
            return True
        try:
            mailbox.queue.put_nowait(message)
        except (Full, ShutDown):
            return False
        mailbox.queued.add(message.message_id)
        MailboxRouter._wake(mailbox)
        return True


class AuthenticatedMailboxPort:
    """Validate every agent operation before entering the host-owned router."""

    def __init__(self, router: MailboxRouter) -> None:
        self._router = router

    def read(self, token: MailboxToken, *, wait: bool) -> list[AgentMessage]:
        return self._router.read(self._router.authenticate(token), wait=wait)

    async def receive(self, token: MailboxToken) -> list[AgentMessage]:
        return await self._router.receive(self._router.authenticate(token))

    def acknowledge(self, token: MailboxToken, message_ids: tuple[str, ...]) -> None:
        self._router.acknowledge(self._router.authenticate(token), message_ids)

    def call(self, token: MailboxToken, child_id: str, task: str, origin: JsonObject) -> str:
        return self._router.call(self._router.authenticate(token), child_id, task, origin)

    async def acall(self, token: MailboxToken, child_id: str, task: str, origin: JsonObject) -> str:
        return self.call(token, child_id, task, origin)

    def report(self, token: MailboxToken, task_id: str, result: TaskResult) -> bool:
        return self._router.report(self._router.authenticate(token), task_id, result)

    async def areport(self, token: MailboxToken, task_id: str, result: TaskResult) -> bool:
        return self.report(token, task_id, result)

    def unfinished(self, token: MailboxToken) -> list[str]:
        return self._router.unfinished(self._router.authenticate(token))

    def tasks_blocked(self, token: MailboxToken) -> bool:
        return self._router.tasks_blocked(self._router.authenticate(token))

    def tasks_reported(self, token: MailboxToken) -> bool:
        return self._router.tasks_reported(self._router.authenticate(token))

    def stop(self, token: MailboxToken) -> None:
        with self._router._lock:
            identity = self._router._tokens.get(token)
            if identity is not None:
                self._router.stop(identity)
