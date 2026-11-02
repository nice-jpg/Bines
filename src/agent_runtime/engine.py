"""One compiled agent and execution policy, with an owned conversation session."""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from uuid import uuid4

from langchain.agents.middleware.types import AgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp, GraphRecursionError
from langgraph.types import Command, Interrupt, StateSnapshot

from nTrace import NTrace, createNTraceHostMiddleware, createNTraceLLMMiddleware, createNTraceToolMiddleware
from nTrace.trace import TraceBinding
from src.agent_messages.text import latest_message_text
from src.agent_messages.types import GraphInput, InputMessage
from src.agent_runtime.contracts import MessageStreamClosed
from src.agent_runtime.conversation import ConversationSession, MessageBlock
from src.common.tracing import trace_client
from src.common.utils.json import JsonValue
from src.common.utils.validation import is_string_mapping

from .contracts import AgentHandle, TaskResult
from .fork import ForkContext
from .middleware.communication import ConversationInputMiddleware, read_pending_input
from .middleware.model_retry import create_model_retry_middleware, is_retryable_model_transport_error
from .middleware.tool_error import ToolErrorMiddleware
from .strategy.middleware import StrategyDispatchMiddleware
from .strategy.registration import StrategyRegistration
from .strategy.registry import StrategyRegistry
from .types.execution import (
    EngineLifecycle,
    EngineResult,
    EngineStatus,
    ExecutionSource,
    ExecutionState,
    InterruptKind,
    YieldReason,
)
from .types.framework import RuntimeGraph, RuntimeMiddleware
from .types.interrupts import extract_interrupts, interrupt_kind, interrupt_output


class AgentEngine[StateT: AgentState = AgentState](ABC):
    """Own business execution and the lifetime, but not the logic, of a session."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        handle: AgentHandle,
        max_iterations: int = 1000,
        session_id: str | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self.handle = handle
        self._model = model
        self._fork_context = ForkContext()
        self._conversation: ConversationSession = ConversationSession(handle)
        self._max_iterations: int = max_iterations
        self._lifecycle: EngineLifecycle = EngineLifecycle.OPEN
        self._execution: ExecutionState = ExecutionState()
        self._state: dict[str, object] = {}
        self._checkpoint_id: str = session_id or f"agent:{uuid4().hex}"
        self._checkpointer: InMemorySaver = InMemorySaver()
        self.agent, self._trace = self._build_graph(model)

    def _build_graph(self, model: str | BaseChatModel) -> tuple[RuntimeGraph, NTrace]:
        from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]

        from .messaging.tools import communication_tools

        tools: list[BaseTool] = [
            *self._create_tools(),
            *communication_tools(self),
        ]
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("Agent tools must have unique names")
        prompt: str = self._system_prompt() + "\n" + AGENT_COMMUNICATION_PROMPT
        name: str = self._name()
        trace: NTrace = NTrace(agent_name=name, system_prompt=prompt, client=trace_client())
        registrations: Sequence[StrategyRegistration[StateT]] = self._strategy_registrations()
        strategy_layers: list[RuntimeMiddleware] = []
        if registrations:
            registry: StrategyRegistry[StateT] = StrategyRegistry()
            for registration in registrations:
                registry.register(registration)
            schema: type = self._strategy_state_schema()
            registry.validate_schema(schema)
            strategy_layers.append(StrategyDispatchMiddleware(state_schema=schema, registry=registry))
        layers: list[RuntimeMiddleware] = [
            createNTraceHostMiddleware(trace),
            create_model_retry_middleware(),
            ConversationInputMiddleware(self._conversation),
            self._fork_context,
            ToolErrorMiddleware(),
            *strategy_layers,
            createNTraceToolMiddleware(trace),
            createNTraceLLMMiddleware(trace),
        ]
        graph: RuntimeGraph = create_agent(
            name=name,
            model=model,
            tools=tools,
            system_prompt=prompt,
            middleware=layers,
            checkpointer=self._checkpointer,
        )
        return graph, trace

    def send_message(self, message: str) -> bool:
        """Report progress to the parent without defining a model tool."""
        return self._conversation.send_message(message)

    def report_interruption(self, result: EngineResult) -> None:
        tasks = self.handle.mailbox.unfinished()
        if tasks:
            self.handle.mailbox.report(
                tasks[-1],
                TaskResult(
                    status="blocked",
                    summary=result.output,
                    data={
                        "interrupt_kind": result.interrupt_kind.value
                        if result.interrupt_kind
                        else "execution"
                    },
                ),
            )

    def call(self, subagent_id: str, task_id: str, task: str) -> dict[str, str]:
        """Send a task or follow-up to your direct child without waiting for its result."""
        topic = self.handle.mailbox.call(subagent_id, task_id, task)
        return {"task_id": topic}

    def accept_report(self, task_id: str) -> bool:
        """Accept a child's final report after verification and remove the delegated task."""
        return self.handle.mailbox.accept_report(task_id)

    def spawn_subagent(self, agent_type: str) -> dict[str, str]:
        """Fork a general child with independent context; submit work separately with call."""
        if agent_type != "general":
            raise ValueError(f"Unsupported child type {agent_type!r}")
        snapshot = self._fork_context.snapshot()
        with self.handle.spawner.child() as handle:
            child = self._create_fork(handle)
            if snapshot:
                child.agent.update_state({"configurable": {"thread_id": child._checkpoint_id}}, snapshot)
                child._state = snapshot
            self.handle.spawner.start(child)
        return {"subagent_id": handle.agent_id}

    def _create_fork(self, handle: AgentHandle) -> AgentEngine:
        raise ValueError("This engine has not declared a safe fork resource contract")

    def report(self, task_id: str, result: TaskResult) -> bool:
        """Report progress, a blocker, failure or completion to the task's parent agent."""
        if task_id not in self.handle.mailbox.unfinished():
            raise PermissionError("Report requires your own delivered, unfinished task")
        prepared = self._prepare_report(result)
        return self.handle.mailbox.report(task_id, prepared)

    def _prepare_report(self, result: TaskResult) -> TaskResult:
        return result

    def kill(self, subagent_id: str) -> dict[str, str]:
        """Stop your direct child and its descendants at the next safe execution boundary."""
        self.handle.spawner.kill(subagent_id)
        return {"subagent_id": subagent_id, "status": "stopping"}

    @property
    def conversation(self) -> ConversationSession:
        return self._conversation

    @abstractmethod
    def _name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _create_tools(self) -> Sequence[BaseTool]:
        raise NotImplementedError

    @abstractmethod
    def _system_prompt(self) -> str:
        raise NotImplementedError

    def _strategy_registrations(self) -> Sequence[StrategyRegistration[StateT]]:
        return ()

    def _strategy_state_schema(self) -> type:
        return AgentState

    def _create_initial_messages(self) -> Sequence[InputMessage]:
        """Build an internal startup assignment; empty means wait for user input.

        A user-role message here is not a user-initiated trace boundary.
        """
        return ()

    def run_turn(self) -> EngineResult:
        if self._lifecycle == EngineLifecycle.CLOSED:
            raise RuntimeError("AgentEngine is closed")
        if self._execution.status in {EngineStatus.CONTINUE_REQUIRED, EngineStatus.RESUME_READY}:
            raise RuntimeError("Continue or resume the current execution before starting another turn")
        if self._lifecycle == EngineLifecycle.STOPPED:
            return self._stopped()
        if self._execution.interrupt_kind is not None:
            token: str = "done" if self._execution.interrupt_kind == InterruptKind.HUMAN else "继续"
            try:
                self._conversation.wait_for_confirmation(token)
            except MessageStreamClosed:
                return self._stopped()
            self._execution.resume_input = token
            self._execution.status = EngineStatus.RESUME_READY
            return EngineResult(EngineStatus.RESUME_READY)
        if self._execution.status is None:
            initial: list[InputMessage] = list(self._create_initial_messages())
            if initial:
                return self._execute(
                    self._input_state(self._prepare_messages(initial)), source=ExecutionSource.INITIAL_TASK
                )
        try:
            block: MessageBlock | None = self._conversation.read_input(wait=True)
        except MessageStreamClosed:
            return self._stopped()
        if block is None:
            raise RuntimeError("Blocking conversation read returned no input")
        messages: list[InputMessage] = self._prepare_messages(
            [HumanMessage(content=block.text, id=block.block_id)]
        )
        return self._execute(self._input_state(messages), source=ExecutionSource.USER_TASK)

    def continue_turn(self) -> EngineResult:
        if (
            self._lifecycle != EngineLifecycle.OPEN
            or self._execution.status != EngineStatus.CONTINUE_REQUIRED
        ):
            raise RuntimeError("No continuation is pending")
        if self._execution.continuation_reason == "new_input":
            messages: list[InputMessage] = list(read_pending_input(self._conversation, self._state))
            return self._execute({"messages": messages}, source=ExecutionSource.CONTINUATION)
        return self._execute(
            {"messages": [self._continuation_message()]}, source=ExecutionSource.CONTINUATION
        )

    async def arun_turn(self) -> EngineResult:
        if self._lifecycle == EngineLifecycle.CLOSED:
            raise RuntimeError("AgentEngine is closed")
        if self._execution.status in {EngineStatus.CONTINUE_REQUIRED, EngineStatus.RESUME_READY}:
            raise RuntimeError("Continue or resume the current execution before starting another turn")
        if self._lifecycle == EngineLifecycle.STOPPED:
            return self._stopped()
        if self._execution.interrupt_kind is not None:
            token = "done" if self._execution.interrupt_kind == InterruptKind.HUMAN else "继续"
            try:
                await self._conversation.await_confirmation(token)
            except MessageStreamClosed:
                return self._stopped()
            self._execution.resume_input = token
            self._execution.status = EngineStatus.RESUME_READY
            return EngineResult(EngineStatus.RESUME_READY)
        if self._execution.status is None:
            initial = list(self._create_initial_messages())
            if initial:
                return await self._aexecute(
                    self._input_state(self._prepare_messages(initial)), source=ExecutionSource.INITIAL_TASK
                )
        try:
            block = await self._conversation.aread_input()
        except MessageStreamClosed:
            return self._stopped()
        messages = self._prepare_messages([HumanMessage(content=block.text, id=block.block_id)])
        return await self._aexecute(self._input_state(messages), source=ExecutionSource.USER_TASK)

    async def acontinue_turn(self) -> EngineResult:
        if (
            self._lifecycle != EngineLifecycle.OPEN
            or self._execution.status != EngineStatus.CONTINUE_REQUIRED
        ):
            raise RuntimeError("No continuation is pending")
        messages = (
            list(read_pending_input(self._conversation, self._state))
            if self._execution.continuation_reason == "new_input"
            else [self._continuation_message()]
        )
        return await self._aexecute({"messages": messages}, source=ExecutionSource.CONTINUATION)

    async def aresume_turn(self) -> EngineResult:
        kind = self._execution.interrupt_kind
        if (
            self._lifecycle != EngineLifecycle.OPEN
            or self._execution.status not in {EngineStatus.INTERRUPTED, EngineStatus.RESUME_READY}
            or kind is None
        ):
            raise RuntimeError("No interrupted execution is pending")
        if self._execution.status != EngineStatus.RESUME_READY:
            if kind not in {InterruptKind.LIVENESS, InterruptKind.MODEL_TRANSPORT, InterruptKind.RECURSION}:
                raise RuntimeError("Human recovery requires a confirmed input")
            self._execution.resume_input = "continue" if kind == InterruptKind.LIVENESS else None
        command = None
        if kind not in {InterruptKind.MODEL_TRANSPORT, InterruptKind.RECURSION, InterruptKind.EXECUTION}:
            command = Command(resume=self._resume_payload(kind, self._execution.resume_input))
        return await self._aexecute(command, source=ExecutionSource.RESUME)

    def resume_turn(self) -> EngineResult:
        kind: InterruptKind | None = self._execution.interrupt_kind
        if (
            self._lifecycle != EngineLifecycle.OPEN
            or self._execution.status not in {EngineStatus.INTERRUPTED, EngineStatus.RESUME_READY}
            or kind is None
        ):
            raise RuntimeError("No interrupted execution is pending")
        if self._execution.status != EngineStatus.RESUME_READY:
            if kind not in {InterruptKind.LIVENESS, InterruptKind.MODEL_TRANSPORT, InterruptKind.RECURSION}:
                raise RuntimeError("Human recovery requires a confirmed input")
            self._execution.resume_input = "continue" if kind == InterruptKind.LIVENESS else None
        command: Command[str] | None = None
        if kind not in {InterruptKind.MODEL_TRANSPORT, InterruptKind.RECURSION, InterruptKind.EXECUTION}:
            command = Command(resume=self._resume_payload(kind, self._execution.resume_input))
        return self._execute(command, source=ExecutionSource.RESUME)

    def close(self) -> None:
        if self._lifecycle == EngineLifecycle.CLOSED:
            return
        self._conversation.close()
        self._lifecycle = EngineLifecycle.CLOSED

    def _tool_yield_reason(self, state: Mapping[str, object]) -> YieldReason | None:
        pending: bool = self._conversation.poll_input()
        if self._conversation.input_closed or self._lifecycle != EngineLifecycle.OPEN:
            return "shutdown_requested"
        return "user_message_pending" if pending else None

    def _stopped(self) -> EngineResult:
        self._lifecycle = EngineLifecycle.STOPPED
        self._execution.status = EngineStatus.STOPPED
        return EngineResult(EngineStatus.STOPPED, "Runtime is shutting down.")

    def _execute(self, input: GraphInput | Command[str] | None, *, source: ExecutionSource) -> EngineResult:
        """Enter this engine's independent graph execution and recovery context.

        A nested Python call is not a LangGraph subgraph. Only this engine's
        explicit configuration crosses the boundary; parent task identity,
        checkpoint storage, callbacks and resume bookkeeping do not.
        """
        config: RunnableConfig = {
            "recursion_limit": self._max_iterations,
            "configurable": {**self._conversation.route, "thread_id": self._checkpoint_id},
        }
        with set_config_context(config) as context:
            return context.run(self._execute_turn, input, source=source, config=config)

    def _execute_turn(
        self, input: GraphInput | Command[str] | None, *, source: ExecutionSource, config: RunnableConfig
    ) -> EngineResult:
        """Run and recover within the execution context established by _execute."""
        continuing: bool = source in {ExecutionSource.CONTINUATION, ExecutionSource.RESUME}
        binding: TraceBinding | None = self._trace.current_binding
        missing_binding: bool = continuing and (binding is None or binding.session_id != self._checkpoint_id)
        if missing_binding:
            # Never let a recovery with lost identity attach to an unrelated active agent.
            logging.getLogger(__name__).warning(
                "Trace binding missing during %s; starting an isolated recovery trace", source
            )
        self._trace.update(
            session_id=self._checkpoint_id,
            resume=continuing,
            user_input_boundary=source == ExecutionSource.USER_TASK or missing_binding,
        )
        try:
            raw: object = self.agent.invoke(input, config=config, durability="exit")  # pyright: ignore[reportUnknownMemberType]
            if not is_string_mapping(raw):
                raise TypeError("Agent graph result must be a mapping")
            self._state = dict(raw)
        except GraphRecursionError:
            return self._failed(InterruptKind.RECURSION, config)
        except GraphBubbleUp:
            raise
        except Exception as error:
            kind: InterruptKind = (
                InterruptKind.MODEL_TRANSPORT
                if is_retryable_model_transport_error(error)
                else InterruptKind.EXECUTION
            )
            return self._failed(kind, config)
        return self._finish_turn()

    async def _aexecute(
        self, input: GraphInput | Command[str] | None, *, source: ExecutionSource
    ) -> EngineResult:
        config: RunnableConfig = {
            "recursion_limit": self._max_iterations,
            "configurable": {**self._conversation.route, "thread_id": self._checkpoint_id},
        }
        with set_config_context(config) as context:
            return await asyncio.create_task(
                self._aexecute_turn(input, source=source, config=config), context=context
            )

    async def _aexecute_turn(
        self, input: GraphInput | Command[str] | None, *, source: ExecutionSource, config: RunnableConfig
    ) -> EngineResult:
        self._trace.update(
            session_id=self._checkpoint_id,
            resume=source in {ExecutionSource.CONTINUATION, ExecutionSource.RESUME},
            user_input_boundary=source == ExecutionSource.USER_TASK,
        )
        try:
            raw = await self.agent.ainvoke(input, config=config, durability="exit")
            if not is_string_mapping(raw):
                raise TypeError("Agent graph result must be a mapping")
            self._state = dict(raw)
        except GraphRecursionError:
            return self._failed(InterruptKind.RECURSION, config)
        except GraphBubbleUp:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("Agent model execution failed")
            kind = (
                InterruptKind.MODEL_TRANSPORT
                if is_retryable_model_transport_error(error)
                else InterruptKind.EXECUTION
            )
            return self._failed(kind, config)
        return self._finish_turn()

    def _finish_turn(self) -> EngineResult:
        self._conversation.finish_turn()
        pending: list[HumanMessage] = read_pending_input(self._conversation, self._state)
        if self._conversation.input_closed:
            return self._stopped()
        interruptions: list[Interrupt | JsonValue] = extract_interrupts(self._state)
        if interruptions:
            self._execution.interrupt_kind = interrupt_kind(interruptions)
            self._execution.status = EngineStatus.INTERRUPTED
            return EngineResult(
                EngineStatus.INTERRUPTED, interrupt_output(interruptions), self._execution.interrupt_kind
            )
        self._execution.interrupt_kind = None
        if pending:
            self._execution.continuation_reason = "new_input"
            self._execution.status = EngineStatus.CONTINUE_REQUIRED
            return EngineResult(EngineStatus.CONTINUE_REQUIRED)
        waiting = self.handle.mailbox.tasks_blocked()
        reported = self.handle.mailbox.tasks_reported()
        result: EngineResult = (
            EngineResult(EngineStatus.COMPLETED)
            if waiting or reported
            else self._evaluate_result(self._state)
        )
        if not waiting and result.status == EngineStatus.COMPLETED and self.handle.mailbox.unfinished():
            self._execution.continuation_reason = "report_required"
            result = EngineResult(EngineStatus.CONTINUE_REQUIRED)
        self._execution.status = result.status
        return result

    def _failed(self, kind: InterruptKind, config: RunnableConfig) -> EngineResult:
        snapshot: StateSnapshot = self.agent.get_state(config)
        self._state = dict(snapshot.values) if is_string_mapping(snapshot.values) else {}
        read_pending_input(self._conversation, self._state)
        if self._conversation.input_closed:
            return self._stopped()
        self._execution.interrupt_kind = kind
        self._execution.status = EngineStatus.INTERRUPTED
        message: str = (
            "Agent execution paused. Resolve the reported error before resuming this checkpoint."
            if kind == InterruptKind.EXECUTION
            else "Agent call interrupted; the same checkpoint is available for continuation."
        )
        return EngineResult(EngineStatus.INTERRUPTED, message, kind)

    def _evaluate_result(self, state: Mapping[str, object]) -> EngineResult:
        return EngineResult(EngineStatus.COMPLETED, latest_message_text(state))

    def _prepare_messages(self, turn_messages: list[InputMessage]) -> list[InputMessage]:
        return turn_messages

    def _input_state(self, messages: list[InputMessage]) -> GraphInput:
        return {"messages": messages}

    def _resume_payload(self, kind: InterruptKind, user_input: JsonValue) -> JsonValue:
        return user_input

    def _continuation_message(self) -> InputMessage:
        if self._execution.continuation_reason == "report_required":
            return {
                "role": "system",
                "content": (
                    "Task execution has not been reported. Use report(task_id, result) for each "
                    "completed/failed task, or continue working. Pending task IDs: "
                    + ", ".join(self.handle.mailbox.unfinished())
                ),
            }
        return {
            "role": "system",
            "content": (
                "<agent_persistence_continuation>\n"
                f"reason: {self._execution.continuation_reason}\n"
                "The task has not produced an accepted completion response. Preserve all existing "
                "context and continue from the current state. If work remains, immediately select "
                "the next valid tool. If work is complete, return the required structured response.\n"
                "Return JSON with resolved (boolean), summary (nonempty string), result (object), "
                "and next_action (null when resolved, otherwise a nonempty string).\n"
                "</agent_persistence_continuation>"
            ),
            "additional_kwargs": {
                "agent_runtime_context": {
                    "kind": "agent_persistence_continuation",
                    "reason": self._execution.continuation_reason,
                }
            },
        }


AGENT_COMMUNICATION_PROMPT = """
You are a persistent agent. Messages contain only task_id, task text and any reported result.
An empty task_id denotes external user input. Routing identities are managed internally.
Process messages in order, including messages arriving during execution.
Use call(subagent_id, task_id, task) only for direct children. Choose a short descriptive task topic
as task_id, unique among your active delegated tasks. Reuse that topic for clarification or revision.
The call returns immediately; do not generate technical UUIDs as task topics.
Results arrive later as report messages. Never fabricate a child result or repeat a task just to poll.
Delegation does not suspend you or disable your tools. Continue independent work while children run.
Parent-child identity does not imply a task dependency. Report your own task based on its actual outcome,
not on whether every child has finished. Do not fabricate results when your task really needs a child result.
Use report(task_id, result) for your own assigned tasks: progress/blocked keeps a task open;
completed/failed/cancelled removes your received task after the report is delivered.
Natural-language final text does not deliver a result to your parent.
When a child reports a final result, verify it. Call accept_report(task_id) only after verification;
otherwise call the same child with the same task_id and specific revision instructions.
Your delegated task stays open until you accept it. Receiving a report does not mean accepting it.
Use blocked only when that task cannot proceed; explain the actual dependency or required input.
When no work can proceed, end this turn and listen. Other runnable tasks may still continue.
After reporting completed work, handle other pending work or return to listening. You remain alive.
Only kill stops a child. Do not kill an agent merely because one task has completed.
Children have no channel access. Their reports reach their parent, which may relay them upward.
"""
