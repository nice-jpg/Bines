"""Long-running agent that owns execution independently of channel implementations."""

import json
import logging
from collections import deque
from functools import partial
from threading import RLock, Thread

from langchain_core.messages import HumanMessage

from src.agent_runtime import AgentCompletionDriver
from src.agent_runtime.communication import AgentExecutionContext, pending_messages
from src.agent_runtime.persistence import resume_token
from src.agent_runtime.runtime import AgentRunResult
from src.message_types import read_input_messages
from src.messaging.messages import IncomingMessage, MessageStreamClosed, OutgoingMessage

from .channel_gateway import ChannelGateway

logger: logging.Logger = logging.getLogger(__name__)
type Conversation = tuple[str, str]


def checkpoint_id(route: Conversation) -> str:
    """Preserve existing Feishu history and namespace every other channel."""
    return f"feishu:{route[1]}" if route[0] == "feishu" else json.dumps(route, ensure_ascii=False)


class LongRunningAgent:
    """Listen across tasks and drive each task to completion or an explicit interruption."""

    def __init__(
        self,
        completion_driver: AgentCompletionDriver,
        gateway: ChannelGateway,
        max_iterations: int,
    ) -> None:
        self._completion_driver: AgentCompletionDriver = completion_driver
        self._gateway: ChannelGateway = gateway
        self._max_iterations: int = max_iterations
        self._pending: dict[Conversation, list[IncomingMessage]] = {}
        self._reply_targets: dict[Conversation, IncomingMessage] = {}
        self._ready: deque[Conversation] = deque()
        self._active: Conversation | None = None
        self._stopping: bool = False
        # Framework tools may call capabilities on their executor threads.
        # No transport can access this lock or any of the protected state.
        self._lock: RLock = RLock()
        self._thread: Thread = Thread(target=self._run, name="long-running-agent")

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        self._thread.join()

    def _accept(self, message: IncomingMessage) -> None:
        route: Conversation = (message.channel_id, message.conversation_id)
        pending: list[IncomingMessage] = self._pending.setdefault(route, [])
        if any(item.message_id == message.message_id for item in pending):
            return
        pending.append(message)
        if route != self._active and route not in self._ready:
            self._ready.append(route)

    def _drain(self) -> None:
        for _ in range(1024):
            try:
                message: IncomingMessage | None = self._gateway.receive_message(wait=False)
            except MessageStreamClosed:
                self._stopping = True
                break
            if message is None:
                break
            self._accept(message)

    def _should_stop(self) -> bool:
        with self._lock:
            self._drain()
            return self._stopping

    def _read_pending(
        self, route: Conversation, seen: set[str | None], acknowledge: bool
    ) -> list[HumanMessage]:
        with self._lock:
            self._drain()
            pending: list[IncomingMessage] = self._pending.setdefault(route, [])
            if acknowledge:
                pending[:] = [message for message in pending if message_identity(message) not in seen]
            return [
                HumanMessage(content=message.text, id=message_identity(message))
                for message in pending
                if message_identity(message) not in seen
            ]

    def _send(self, target: IncomingMessage, text: str) -> bool:
        if not text.strip():
            return False
        return self._gateway.send_message(
            OutgoingMessage(target.channel_id, target.conversation_id, text, target.reply_to)
        )

    def _run(self) -> None:
        try:
            while True:
                with self._lock:
                    self._drain()
                    if self._stopping:
                        break
                    route: Conversation | None = self._ready.popleft() if self._ready else None
                    self._active = route
                if route is None:
                    try:
                        message: IncomingMessage | None = self._gateway.receive_message(wait=True)
                    except MessageStreamClosed:
                        with self._lock:
                            self._stopping = True
                        break
                    if message is not None:
                        with self._lock:
                            self._accept(message)
                    continue
                with self._lock:
                    target: IncomingMessage = self._reply_targets.setdefault(route, self._pending[route][0])
                try:
                    self._execute(route, target)
                except Exception:
                    logger.exception("Agent execution failed for %s", route)
                    self._send(target, "任务运行异常；上下文已保留，请检查日志后继续。")
                finally:
                    with self._lock:
                        self._active = None
        finally:
            try:
                # Draining input means delivery to local buffers, not task execution.
                for pending in self._pending.values():
                    if pending:
                        self._send(
                            pending[0], f"Agent 已关闭，仍有 {len(pending)} 条消息未执行，请重新发送。"
                        )
            finally:
                try:
                    self._completion_driver.close()
                finally:
                    self._gateway.finish_output()

    def _execute(self, route: Conversation, target: IncomingMessage) -> None:
        session_id: str = checkpoint_id(route)
        with self._lock:
            starting_ids: set[str] = {item.message_id for item in self._pending[route]}
        context: AgentExecutionContext = AgentExecutionContext(
            partial(self._read_pending, route), partial(self._send, target), self._should_stop
        )
        if self._completion_driver.has_pending_interrupt(session_id):
            kind: str | None = self._completion_driver.pending_interrupt_kind(session_id)
            expected: str = resume_token(kind)
            # Supplementary text remains pending until the explicit resume token arrives.
            with self._lock:
                resume: IncomingMessage | None = next(
                    (item for item in self._pending[route] if item.text == expected), None
                )
                if resume is not None:
                    self._pending[route].remove(resume)
            if resume is None:
                self._send(target, f"补充信息已保存。当前任务等待恢复，请回复“{expected}”继续。")
                return
            result: AgentRunResult = self._completion_driver.resume_turn(
                session_id=session_id,
                user_input=expected,
                max_iterations=self._max_iterations,
                context=context,
            )
        else:
            result = self._completion_driver.run_turn(
                [HumanMessage(content=target.text, id=message_identity(target))],
                session_id=session_id,
                max_iterations=self._max_iterations,
                context=context,
            )
        pending_messages(context, read_input_messages(result.state.get("messages", [])))
        if result.output:
            self._send(target, result.output)
        with self._lock:
            self._drain()
            if not result.interrupted:
                self._reply_targets.pop(route, None)
            newly_received: bool = any(item.message_id not in starting_ids for item in self._pending[route])
            if (
                (not result.interrupted or newly_received)
                and self._pending[route]
                and route not in self._ready
            ):
                self._ready.append(route)
            if not self._pending[route]:
                self._pending.pop(route, None)


def message_identity(message: IncomingMessage) -> str:
    return json.dumps([message.channel_id, message.conversation_id, message.message_id], ensure_ascii=False)
