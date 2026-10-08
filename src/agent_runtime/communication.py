"""Session-scoped communication with checkpoint-acknowledged input delivery."""

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import logging
from threading import RLock
from uuid import uuid4
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

logger = logging.getLogger(__name__)


class RuntimeCommunication:
    """Receive external input and send reports for one business session."""

    def __init__(self, sender: Callable[[str], bool]):
        self._sender = sender
        self._lock = RLock()
        self._closed = False
        self._on_close = lambda: None
        self._inboxes = {"root": []}
        self._root = _Inbox(self, "root")

    def talk(self, message: str) -> bool:
        with self._lock:
            sender = self._sender
            if self._closed or not isinstance(message, str) or not message.strip():
                return False
        try:
            return sender(message) is True
        except Exception:
            logger.exception("Runtime message delivery failed")
            return False

    def receive(self, message: str) -> bool:
        if not isinstance(message, str) or not message.strip():
            return False
        with self._lock:
            if self._closed:
                return False
            item = HumanMessage(content=message, id=f"runtime-input:{uuid4().hex}")
            for queue in self._inboxes.values():
                queue.append(item)
            return True

    def close(self) -> None:
        """Stop accepting input; active graphs stop at their next model boundary."""
        with self._lock:
            self._closed = True
            self._sender = None
        self._on_close()

    @property
    def pending(self) -> bool:
        """Whether the parent still has unacknowledged external input."""
        with self._lock:
            return bool(self._inboxes["root"])

    def _close_if_idle(self) -> bool:
        """Atomically finish a session only when all accepted input is consumed."""
        with self._lock:
            if self._inboxes["root"]:
                return False
            self._closed = True
            self._sender = None
        self._on_close()
        return True

    @contextmanager
    def _subscribe(self):
        with self._lock:
            key = uuid4().hex
            self._inboxes[key] = list(self._inboxes["root"])
        try:
            yield _Inbox(self, key)
        finally:
            with self._lock:
                self._inboxes.pop(key, None)


@dataclass
class _Inbox:
    channel: RuntimeCommunication
    key: str

    def pending(self, messages):
        seen = {m.get("id") if isinstance(m, Mapping) else m.id for m in messages}
        with self.channel._lock:
            queue = self.channel._inboxes[self.key]
            queue[:] = [m for m in queue if m.id not in seen]
            return list(queue)


class CommunicationMiddleware(AgentMiddleware):
    """Persist external input before business before-model hooks run."""

    def before_model(self, state, runtime):
        inbox = runtime.context
        if not isinstance(inbox, _Inbox):
            return None
        if inbox.channel._closed:
            interrupt({"kind": "shutdown", "message": "Runtime is shutting down."})
        pending = inbox.pending(state.get("messages") or [])
        return {"messages": pending} if pending else None

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    def after_model(self, state, runtime):
        inbox = runtime.context
        if isinstance(inbox, _Inbox) and inbox.channel._closed:
            interrupt({"kind": "shutdown", "message": "Runtime is shutting down."})

    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)


def talk(message: str, runtime: ToolRuntime[Any]) -> bool:
    """Report a current problem to the user without pausing or waiting for a reply."""
    inbox = runtime.context
    return inbox.channel.talk(message) if isinstance(inbox, _Inbox) else False


def create_talk_tool():
    return StructuredTool.from_function(talk)


def tool_yield_reason(runtime: ToolRuntime[Any]) -> str | None:
    """Non-consuming signal for a long-running tool at a safe action boundary."""
    inbox = runtime.context
    if not isinstance(inbox, _Inbox):
        return None
    seen = {message.id for message in runtime.state.get("messages", [])}
    with inbox.channel._lock:
        if inbox.channel._closed:
            return "shutdown_requested"
        if any(message.id not in seen for message in inbox.channel._inboxes[inbox.key]):
            return "user_message_pending"
    return None
