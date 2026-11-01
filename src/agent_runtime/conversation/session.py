"""Engine-owned message history and acknowledged dialogue blocks."""

import json
import logging
from uuid import uuid4

from src.agent_runtime.contracts import AgentHandle, AgentMessage, MessageStreamClosed, TaskResult

from .message_block import MessageBlock

logger: logging.Logger = logging.getLogger(__name__)


class ConversationSession:
    def __init__(self, handle: AgentHandle) -> None:
        self._handle = handle
        self._backlog: list[AgentMessage] = []
        self._pending: tuple[MessageBlock, list[AgentMessage]] | None = None
        self.conversations: dict[tuple[str | None, str], list[AgentMessage]] = {}
        self._seen: set[str] = set()
        self._acknowledged: set[str] = set()
        self._confirmations: set[str] = set()
        self._closed: bool = False
        self._input_closed: bool = False

    @property
    def input_closed(self) -> bool:
        return self._input_closed

    @property
    def route(self) -> dict[str, str]:
        return {"agent_id": self._handle.agent_id}

    @property
    def pending_count(self) -> int:
        return len(self._backlog) + (len(self._pending[1]) if self._pending is not None else 0)

    @property
    def pending_block(self) -> MessageBlock | None:
        return self._pending[0] if self._pending is not None else None

    def _ensure_open(self) -> None:
        if self._closed:
            raise MessageStreamClosed("ConversationSession is closed")

    def _collect(self, *, wait: bool) -> None:
        self._ensure_open()
        try:
            batch = self._handle.mailbox.read(wait=wait)
        except MessageStreamClosed:
            self._input_closed = True
            raise
        self._stage(batch)

    def _stage(self, batch: list[AgentMessage]) -> None:
        for message in batch:
            if message.message_id in self._seen:
                continue
            self._seen.add(message.message_id)
            self._backlog.append(message)
            if message.session_id is not None:
                self.conversations.setdefault((message.channel_id, message.session_id), []).append(message)
        self._handle.mailbox.acknowledge(batch)

    async def aread_input(self) -> MessageBlock:
        block = self.read_input(wait=False)
        while block is None:
            try:
                self._stage(await self._handle.mailbox.receive())
            except MessageStreamClosed:
                self._input_closed = True
                raise
            block = self.read_input(wait=False)
        return block

    async def await_confirmation(self, expected: str) -> None:
        while True:
            messages = [*(self._pending[1] if self._pending else []), *self._backlog]
            for message in messages:
                if message.message_id not in self._confirmations and message.text.strip() == expected:
                    self._confirmations.add(message.message_id)
                    return
            try:
                self._stage(await self._handle.mailbox.receive())
            except MessageStreamClosed:
                self._input_closed = True
                raise

    def poll_input(self) -> bool:
        """Collect arrivals without sealing a block before the next model boundary."""
        self._ensure_open()
        try:
            self._collect(wait=False)
        except MessageStreamClosed:
            pass
        return self.pending_count > 0

    def read_input(self, *, wait: bool) -> MessageBlock | None:
        self._ensure_open()
        if self._pending is not None:
            try:
                self._collect(wait=False)
            except MessageStreamClosed:
                pass
            return self._pending[0]
        while True:
            try:
                self._collect(wait=wait and not self._backlog)
            except MessageStreamClosed:
                if not self._backlog:
                    raise
            if self._backlog:
                if self._backlog[0].batch_id is not None:
                    members = [self._backlog.pop(0)]
                else:
                    boundary = next(
                        (i for i, message in enumerate(self._backlog) if message.batch_id is not None),
                        len(self._backlog),
                    )
                    members = self._backlog[:boundary]
                    del self._backlog[:boundary]
                block: MessageBlock = self._build_block(members)
                self._pending = (block, members)
                return block
            if not wait:
                return None

    def wait_for_confirmation(self, expected: str) -> None:
        self._ensure_open()
        while True:
            messages: list[AgentMessage] = [
                *(self._pending[1] if self._pending is not None else []),
                *self._backlog,
            ]
            for message in messages:
                if message.message_id not in self._confirmations and message.text.strip() == expected:
                    self._confirmations.add(message.message_id)
                    return
            self._collect(wait=True)

    def acknowledge(self, block_id: str) -> None:
        self._ensure_open()
        if block_id in self._acknowledged:
            return
        if self._pending is None or self._pending[0].block_id != block_id:
            raise ValueError("No matching pending message block")
        self._acknowledged.add(block_id)
        self._pending = None

    def send_message(self, text: str) -> bool:
        self._ensure_open()
        tasks = self._handle.mailbox.unfinished()
        if not tasks:
            return False
        return self._handle.mailbox.report(tasks[-1], TaskResult(status="progress", summary=text))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.pending_count:
            logger.warning("Conversation closed with %d unacknowledged messages", self.pending_count)

    def _build_block(self, messages: list[AgentMessage]) -> MessageBlock:
        lines: list[str] = [
            "Process these messages in sequence order. Keep conversation and task identities separate:"
        ]
        for message in messages:
            record = {
                "sequence": message.sequence,
                "kind": message.kind,
                "sender_id": message.sender_id,
                "task_id": message.task_id,
                "task_status": message.result.status
                if message.result
                else ("submitted" if message.kind == "task" else None),
                "text": message.text,
            }
            if message.session_id is not None:
                record["session_id"] = message.session_id
            if message.result is not None:
                record["result"] = message.result.model_dump()
            if message.origin:
                record["origin"] = message.origin
            if message.channel_id is not None:
                record["channel_id"] = message.channel_id
            if message.batch_id is not None:
                record["batch_id"] = message.batch_id
            if message.kind == "user":
                record["sender_name"] = message.sender_name
            lines.append(json.dumps(record, ensure_ascii=False))
        return MessageBlock(
            f"conversation:{messages[0].batch_id or uuid4().hex}",
            "\n".join(lines),
            tuple(message.text for message in messages),
        )
