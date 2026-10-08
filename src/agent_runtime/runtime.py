"""Framework runtime: input translation, checkpoints and normalized outcomes."""
from __future__ import annotations
from dataclasses import dataclass
import itertools
from contextlib import nullcontext
from collections.abc import Callable
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence
from langchain_core.messages import BaseMessage
from langgraph.types import Command, Interrupt
from langgraph.errors import GraphBubbleUp, GraphRecursionError
from ._model_retry import is_retryable_model_transport_error
from .communication import CommunicationMiddleware, RuntimeCommunication, _Inbox, create_talk_tool

@dataclass(frozen=True)
class AgentRunResult:
    """Normalized result returned by the LangChain agent harness."""

    output: str
    state: Mapping[str, Any]
    stopped_by: str
    interrupted: bool = False
    interrupts: list[Any] | None = None
    summary: list[Any] | None = None
    structured_response: Mapping[str, Any] | None = None
    completion_allowed: bool = True


@dataclass(frozen=True)
class _PendingInterrupt:
    thread_id: str
    kind: str


class AgentRuntime:
    """Reusable agent runtime that separates initialization from interaction."""

    def __init__(self, *, name: str, model, tools, system_prompt: str, middleware=()) -> None:
        from ._stability import build_graph
        tools = list(tools)
        if any(tool.name == "talk" for tool in tools):
            raise ValueError("talk is owned by agent_runtime")
        self.agent, self._trace = build_graph(name, model, [*tools, create_talk_tool()], system_prompt,
                                             [CommunicationMiddleware(), *middleware])
        self._communications = {}
        self._active_channels: set[RuntimeCommunication] = set()
        self._communication_lock = RLock()
        self._closing = False
        self.sessions: dict[str, list[Any]] = {}
        self._stateless_counter = itertools.count(1)
        self._pending_interrupts: dict[str, _PendingInterrupt] = {}

    def bind_communication(self, session_id: str, sender: Callable[[str], bool]) -> RuntimeCommunication:
        with self._communication_lock:
            if self._closing:
                raise RuntimeError("Runtime is closing")
            channel = self._communications.get(session_id)
            if channel is None or channel._closed:
                channel = RuntimeCommunication(sender)
                self._communications[session_id] = channel
                def release():
                    with self._communication_lock:
                        if self._communications.get(session_id) is channel:
                            self._communications.pop(session_id, None)
                channel._on_close = release
            return channel

    def close(self) -> None:
        """Request a cooperative stop without interrupting active device operations."""
        with self._communication_lock:
            self._closing = True
            for channel in set(self._communications.values()) | self._active_channels:
                channel.close()

    def _invoke_graph(self, input, config, inbox=None):
        # Even an unbound invocation needs a cooperative shutdown signal for long tools.
        temporary = inbox is None
        inbox = inbox if inbox is not None else RuntimeCommunication(lambda _: False)._root
        with self._communication_lock:
            self._active_channels.add(inbox.channel)
        try:
            return self._invoke_with_inbox(input, config, inbox)
        finally:
            with self._communication_lock:
                self._active_channels.discard(inbox.channel)
            if temporary:
                inbox.channel.close()

    def _invoke_with_inbox(self, input, config, inbox: _Inbox):
        while True:
            if self._closing:
                snapshot = self.agent.get_state(config)
                return {**snapshot.values, "__interrupt__": [{"kind": "shutdown", "message": "Runtime is shutting down."}]}
            state = self.agent.invoke(
                input, config=config, durability="exit", context=inbox,
            )
            if _extract_interrupts(state) or inbox.channel._closed:
                return state
            pending = inbox.pending(state.get("messages") or [])
            if not pending:
                return state
            # A final model reply can race an external arrival. Continue the same checkpoint.
            input = {"messages": pending}

    def run_turn(
        self,
        messages: Iterable[BaseMessage | Mapping[str, Any]],
        *,
        session_id: str | None = None,
        max_iterations: int = 1000,
    ) -> AgentRunResult:
        """Run one interaction turn without rebuilding the model or agent."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")

        turn_messages = list(messages)
        prepared_messages = self._prepare_messages(turn_messages, session_id=session_id)
        thread_id = self._thread_id_for_turn(session_id)
        self._trace.update(
            session_id=thread_id,
            user_input_boundary=True,
        )
        input_state = self._input_state(prepared_messages)
        config = _invoke_config(max_iterations, thread_id)
        return self._execute(input_state, config=config, session_id=session_id,
                             thread_id=thread_id, prepared_messages=prepared_messages)

    def resume_turn(
        self,
        *,
        session_id: str,
        user_input: Any | None = None,
        max_iterations: int = 1000,
    ) -> AgentRunResult:
        """Resume a previously interrupted LangGraph thread."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        pending = self._pending_interrupts.get(session_id)
        if not pending:
            raise ValueError(f"No interrupted agent workflow is pending for session: {session_id}")

        if pending.kind in {"model_transport", "recursion", "execution"}:
            resume_command: Command[Any] | None = None
        else:
            resume_command = Command(resume=self._resume_payload(pending.kind, user_input))
        config = _invoke_config(max_iterations, pending.thread_id)
        self._trace.update(session_id=session_id, resume=True)
        return self._execute(resume_command, config=config, session_id=session_id,
                             thread_id=pending.thread_id, prepared_messages=self.sessions.get(session_id, []))

    def _continue_turn(
        self,
        *,
        session_id: str,
        reason: str,
        max_iterations: int = 1000,
    ) -> AgentRunResult:
        """Continue a completed graph call on the same checkpoint without new user input."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if self.has_pending_interrupt(session_id):
            return self.resume_turn(
                session_id=session_id,
                max_iterations=max_iterations,
            )
        instruction = {
            "role": "system",
            "content": (
                "<agent_persistence_continuation>\n"
                f"reason: {reason}\n"
                "The task has not produced an accepted completion response. Preserve all existing "
                "context and continue from the current state. If work remains, immediately select "
                "the next valid tool. If work is complete, return the required structured response.\n"
                'Return JSON with resolved (boolean), summary (nonempty string), result (object), '
                'and next_action (null when resolved, otherwise a nonempty string).\n'
                "</agent_persistence_continuation>"
            ),
            "additional_kwargs": {
                "agent_runtime_context": {
                    "kind": "agent_persistence_continuation",
                    "reason": reason,
                }
            },
        }
        thread_id = session_id
        config = _invoke_config(max_iterations, thread_id)
        self._trace.update(session_id=session_id, resume=True)
        return self._execute({"messages": [instruction]}, config=config, session_id=session_id,
                             thread_id=thread_id, prepared_messages=self.sessions.get(session_id, []))

    def _execute(self, input, *, config, session_id, thread_id, prepared_messages):
        try:
            with self._communication_lock:
                channel = self._communications.get(thread_id)
            state = self._invoke_graph(input, config, channel._root if channel else None)
        except GraphRecursionError:
            return self._failed_call("recursion", None, session_id=session_id,
                                     thread_id=thread_id, config=config)
        except GraphBubbleUp:
            raise
        except Exception as error:
            kind = "model_transport" if is_retryable_model_transport_error(error) else "execution"
            return self._failed_call(kind, error, session_id=session_id,
                                     thread_id=thread_id, config=config)
        return self._normalize_result(state, prepared_messages=prepared_messages,
                                      session_id=session_id, thread_id=thread_id)

    def _prepare_messages(self, turn_messages: list[Any], *, session_id: str | None) -> list[Any]:
        # The checkpointer already owns history; submit only the new input.
        return turn_messages

    def _input_state(self, messages: list[Any]) -> dict[str, Any]:
        return {"messages": messages}

    def _completion_allowed(self, state: Mapping[str, Any]) -> bool:
        return True

    def _resume_payload(self, kind: str, user_input: Any) -> Any:
        """Pass native LangGraph resume input through without business interpretation."""
        return user_input

    def invoke(self, input, config=None, *, context=None):
        """Run a child graph with its own checkpoint and trace identity."""
        config = dict(config or {})
        config["configurable"] = dict(config.get("configurable") or {})
        config["configurable"].setdefault("thread_id", self._thread_id_for_turn(None))
        self._trace.update(session_id=config["configurable"]["thread_id"])
        subscription = context.channel._subscribe() if isinstance(context, _Inbox) else nullcontext(None)
        with subscription as inbox:
            return self._invoke_graph(input, config, inbox)

    def has_pending_interrupt(self, session_id: str) -> bool:
        return session_id in self._pending_interrupts

    def pending_interrupt_kind(self, session_id: str) -> str | None:
        pending = self._pending_interrupts.get(session_id)
        return pending.kind if pending is not None else None

    def _normalize_result(
        self,
        state: Mapping[str, Any],
        *,
        prepared_messages: Sequence[Any],
        session_id: str | None,
        thread_id: str,
    ) -> AgentRunResult:
        interrupts = _extract_interrupts(state)
        interrupt_kind = _interrupt_kind(interrupts)
        if session_id and interrupts:
            self._pending_interrupts[session_id] = _PendingInterrupt(thread_id, interrupt_kind)
        elif session_id:
            self._pending_interrupts.pop(session_id, None)
            self.sessions[session_id] = list(state.get("messages") or prepared_messages)
        stopped_by = (
            f"{interrupt_kind}_interrupt"
            if interrupts
            else "completed"
            if self._completion_allowed(state)
            else "create_agent"
        )
        output = _interrupt_output(interrupts) if interrupts else _latest_text(state)
        return AgentRunResult(
            output=output,
            state=state,
            stopped_by=stopped_by,
            interrupted=bool(interrupts),
            interrupts=interrupts,
            completion_allowed=self._completion_allowed(state),
        )

    def _thread_id_for_turn(self, session_id: str | None) -> str:
        if session_id:
            return session_id
        return f"stateless:{id(self)}:{next(self._stateless_counter)}"

    def _failed_call(self, kind, error, *, session_id, thread_id, config) -> AgentRunResult:
        snapshot = self.agent.get_state(config)
        state = snapshot.values if isinstance(snapshot.values, Mapping) else {}
        detail = {"type": kind, "retryable": session_id is not None and kind != "execution"}
        if error is not None:
            detail["error_type"] = type(error).__name__
        if session_id is not None:
            self._pending_interrupts[session_id] = _PendingInterrupt(thread_id, kind)
        message = (
            "Agent execution paused. Resolve the reported error before resuming this checkpoint."
            if kind == "execution"
            else "Agent call interrupted; the same checkpoint is available for continuation."
        )
        if session_id is None:
            message = "Agent call failed without a session; checkpoint recovery is not available."
        return AgentRunResult(output=message, state=state,
                              stopped_by=f"{kind}_{'interrupt' if session_id is not None else 'error'}",
                              interrupted=session_id is not None, interrupts=[detail],
                              completion_allowed=False)


def _invoke_config(max_iterations: int, thread_id: str) -> dict[str, Any]:
    return {
        "recursion_limit": max_iterations,
        "configurable": {"thread_id": thread_id},
    }


def _extract_interrupts(state: Mapping[str, Any]) -> list[Any]:
    interrupts = state.get("__interrupt__") or state.get("interrupts") or []
    if interrupts is None:
        return []
    if isinstance(interrupts, list):
        return interrupts
    if isinstance(interrupts, tuple):
        return list(interrupts)
    return [interrupts]


def _latest_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""

    latest = messages[-1]
    content = latest.get("content") if isinstance(latest, Mapping) else latest.content
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)


def _interrupt_value(value: Any) -> Any:
    return value.value if isinstance(value, Interrupt) else value


def _interrupt_kind(interrupts: Sequence[Any]) -> str:
    for item in interrupts:
        value = _interrupt_value(item)
        if isinstance(value, Mapping) and value.get("kind") in {"liveness", "shutdown"}:
            return value["kind"]
    return "human"


def _interrupt_output(interrupts: Sequence[Any]) -> str:
    for item in interrupts:
        value = _interrupt_value(item)
        if isinstance(value, Mapping):
            message = value.get("message")
            if isinstance(message, str):
                return message
    return ""
