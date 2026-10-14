"""One compiled agent and execution policy, with an owned conversation session."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp, GraphRecursionError
from langgraph.types import Command, Interrupt, StateSnapshot

from nTrace import NTrace, createNTraceHostMiddleware, createNTraceLLMMiddleware, createNTraceToolMiddleware
from src.agent_runtime.conversation import ConversationSession, MessageBlock
from src.agent_runtime.messaging.messages import MessageStreamClosed
from src.application.trace import trace_client
from src.framework_data import is_string_mapping
from src.json_types import JsonValue
from src.message_text import latest_message_text
from src.message_types import GraphInput, InputMessage

from .middleware.communication import ConversationInputMiddleware, create_talk_tool, read_pending_input
from .middleware.model_retry import create_model_retry_middleware, is_retryable_model_transport_error
from .middleware.tool_error import ToolErrorMiddleware
from .types.execution import (
    EngineLifecycle,
    EngineResult,
    EngineStatus,
    ExecutionState,
    InterruptKind,
    YieldReason,
)
from .types.framework import RuntimeGraph, RuntimeMiddleware
from .types.interrupts import extract_interrupts, interrupt_kind, interrupt_output


class AgentEngine(ABC):
    """Own business execution and the lifetime, but not the logic, of a session."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        gateway_id: str,
        max_iterations: int = 1000,
        session_id: str | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if not gateway_id.strip():
            raise ValueError("gateway_id must be a nonempty string")
        self._gateway_id: str = gateway_id
        self._conversation: ConversationSession = ConversationSession(gateway_id)
        self._max_iterations: int = max_iterations
        self._lifecycle: EngineLifecycle = EngineLifecycle.OPEN
        self._execution: ExecutionState = ExecutionState()
        self._state: dict[str, object] = {}
        self._checkpoint_id: str = session_id or f"agent:{uuid4().hex}"
        self.agent, self._trace = self._build_graph(model)

    def _build_graph(self, model: str | BaseChatModel) -> tuple[RuntimeGraph, NTrace]:
        from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]

        tools: list[BaseTool] = list(self._create_tools())
        if any(tool.name == "talk" for tool in tools):
            raise ValueError("talk is owned by AgentEngine")
        prompt: str = self._system_prompt()
        name: str = self._name()
        trace: NTrace = NTrace(agent_name=name, system_prompt=prompt, client=trace_client())
        layers: list[RuntimeMiddleware] = [
            createNTraceHostMiddleware(trace),
            create_model_retry_middleware(),
            ConversationInputMiddleware(self._conversation),
            *self._create_middlewares(),
            ToolErrorMiddleware(),
            createNTraceToolMiddleware(trace),
            createNTraceLLMMiddleware(trace),
        ]
        graph: RuntimeGraph = create_agent(
            name=name,
            model=model,
            tools=[*tools, create_talk_tool()],
            system_prompt=prompt,
            middleware=layers,
            checkpointer=InMemorySaver(),
        )
        return graph, trace

    @property
    def gateway_id(self) -> str:
        return self._gateway_id

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

    def _create_middlewares(self) -> Sequence[RuntimeMiddleware]:
        return ()

    def _create_initial_messages(self) -> Sequence[InputMessage]:
        """Build startup input lazily on the first run; empty means wait for a conversation."""
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
                return self._execute(self._input_state(self._prepare_messages(initial)), new_turn=True)
        try:
            block: MessageBlock | None = self._conversation.read_input(wait=True)
        except MessageStreamClosed:
            return self._stopped()
        if block is None:
            raise RuntimeError("Blocking conversation read returned no input")
        messages: list[InputMessage] = self._prepare_messages(
            [HumanMessage(content=block.text, id=block.block_id)]
        )
        return self._execute(self._input_state(messages), new_turn=True)

    def continue_turn(self) -> EngineResult:
        if (
            self._lifecycle != EngineLifecycle.OPEN
            or self._execution.status != EngineStatus.CONTINUE_REQUIRED
        ):
            raise RuntimeError("No continuation is pending")
        if self._execution.continuation_reason == "new_input":
            messages: list[InputMessage] = list(read_pending_input(self._conversation, self._state))
            return self._execute({"messages": messages}, new_turn=False)
        return self._execute({"messages": [self._continuation_message()]}, new_turn=False)

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
        return self._execute(command, new_turn=False)

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

    def _execute(self, input: GraphInput | Command[str] | None, *, new_turn: bool) -> EngineResult:
        config: RunnableConfig = {
            "recursion_limit": self._max_iterations,
            "configurable": {**self._conversation.route, "thread_id": self._checkpoint_id},
        }
        self._trace.update(session_id=self._checkpoint_id, user_input_boundary=new_turn)
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
        result: EngineResult = self._evaluate_result(self._state)
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
