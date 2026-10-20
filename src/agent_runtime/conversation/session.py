"""Engine-owned fixed conversation, raw backlog and acknowledged dialogue blocks."""

import logging
from uuid import uuid4

from src.agent_runtime.messaging.messages import IncomingMessage, MessageStreamClosed, OutgoingMessage
from src.application.gateway_manager import GatewayManager

from .message_block import MessageBlock

logger: logging.Logger = logging.getLogger(__name__)


class ConversationSession:
    def __init__(self, gateway_id: str) -> None:
        if not gateway_id:
            raise ValueError("gateway_id must not be empty")
        self._gateway_id: str = gateway_id
        self._address: tuple[str, str] | None = None
        self._backlog: list[IncomingMessage] = []
        self._pending: tuple[MessageBlock, list[IncomingMessage]] | None = None
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
        address: tuple[str, str] | None = self._address
        if address is None:
            return {"gateway_id": self._gateway_id}
        return {"gateway_id": self._gateway_id, "channel_id": address[0], "conversation_id": address[1]}

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
            batch: list[IncomingMessage] = GatewayManager.instance().receive_messages(
                self._gateway_id, wait=wait
            )
        except MessageStreamClosed:
            self._input_closed = True
            raise
        for message in batch:
            address: tuple[str, str] = (message.channel_id, message.conversation_id)
            if self._address is None:
                self._address = address
            if address != self._address:
                logger.warning("Rejected input from another conversation: %s", address)
                continue
            if message.message_id in self._seen:
                continue
            self._seen.add(message.message_id)
            self._backlog.append(message)

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
                members: list[IncomingMessage] = self._backlog
                self._backlog = []
                block: MessageBlock = self._build_block(members)
                self._pending = (block, members)
                return block
            if not wait:
                return None

    def wait_for_confirmation(self, expected: str) -> None:
        self._ensure_open()
        while True:
            messages: list[IncomingMessage] = [
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
        if self._address is None:
            raise RuntimeError("Conversation has no bound address")
        return GatewayManager.instance().send_message(self._gateway_id, OutgoingMessage(*self._address, text))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.pending_count:
            logger.warning("Conversation closed with %d unacknowledged messages", self.pending_count)

    def _build_block(self, messages: list[IncomingMessage]) -> MessageBlock:
        names: dict[str, set[str]] = {}
        for message in messages:
            name: str = message.sender_name or message.sender_id
            names.setdefault(name, set()).add(message.sender_id)
        lines: list[str] = ["以下是同一会话按接收顺序积攒的消息，请结合整段对话理解当前需求："]
        for message in messages:
            name = message.sender_name or message.sender_id
            label: str = f"{name} ({message.sender_id})" if len(names[name]) > 1 else name
            label = label.replace("\n", " ").replace("\r", " ")
            text: str = message.text.replace("\r\n", "\n").replace("\r", "\n")
            lines.append(f"{label}: {text.replace(chr(10), chr(10) + '    ')}")
        return MessageBlock(
            f"conversation:{uuid4().hex}", "\n".join(lines), tuple(message.text for message in messages)
        )
