"""Independent Engine checkpoint ownership under a real parent tool task."""

from collections.abc import Mapping

import pytest
from engine_helpers import Model, ScriptedEngine
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import StructuredTool

from src.agent_messages.types import GraphInput, InputMessage
from src.agent_runtime.types.execution import EngineResult, EngineStatus, InterruptKind


class CheckpointEngine(ScriptedEngine):
    def _input_state(self, messages: list[InputMessage]) -> GraphInput:
        state: GraphInput = super()._input_state(messages)
        state["probe"] = {"run_id": "child-run", "current_merchant": "A"}
        return state

    def _evaluate_result(self, state: Mapping[str, object]) -> EngineResult:
        result: EngineResult = super()._evaluate_result(state)
        if result.output == "first":
            self._execution.continuation_reason = "unfinished"
            return EngineResult(EngineStatus.CONTINUE_REQUIRED)
        return result


@pytest.mark.parametrize("fail_continuation", [False, True])
def test_nested_engine_owns_checkpoint_and_restores_all_state(fail_continuation: bool) -> None:
    completed: list[bool] = []
    child_model: Model = Model(replies=[AIMessage("first"), AIMessage("done"), AIMessage("done")])
    child: CheckpointEngine = CheckpointEngine(
        model=child_model, initial_messages=[HumanMessage("child assignment")]
    )

    def fail_once(index: int) -> None:
        if fail_continuation and index == 1:
            raise ValueError("controlled model failure")

    child_model.on_call = fail_once

    def delegate() -> str:
        """Run and resume an independent Engine inside the parent tool task."""
        parent_context: RunnableConfig | None = var_child_runnable_config.get()
        assert parent_context is not None
        try:
            assert child.run_turn().status == EngineStatus.CONTINUE_REQUIRED
            assert var_child_runnable_config.get() is parent_context
            result: EngineResult = child.continue_turn()
            if fail_continuation:
                assert result.status == EngineStatus.INTERRUPTED
                assert result.interrupt_kind == InterruptKind.EXECUTION
                # The error-path get_state must read the same independent saver.
                assert child._state["probe"]["run_id"] == "child-run"
                assert child._state["active_strategy"] == "general"
                child._execution.status = EngineStatus.RESUME_READY
                result = child.resume_turn()
            assert result.status == EngineStatus.COMPLETED
            assert child._state["probe"] == {"run_id": "child-run", "current_merchant": "A"}
            assert child._state["active_strategy"] == "general"
            assert any(message.content == "child assignment" for message in child_model.seen[-1])
            assert var_child_runnable_config.get() is parent_context
            completed.append(True)
            return result.output
        finally:
            child.close()

    parent_model: Model = Model(replies=[
        AIMessage("", tool_calls=[{"name": "delegate", "args": {}, "id": "d"}]),
        AIMessage("parent done"),
    ])
    parent: ScriptedEngine = ScriptedEngine(
        model=parent_model, tools=[StructuredTool.from_function(delegate)],
        initial_messages=[HumanMessage("parent assignment")],
    )
    try:
        assert parent.run_turn().output == "parent done"
        assert completed == [True]
        # Both deliberately use thread_id=test: saver ownership, not unique IDs,
        # must isolate the graphs. Neither may contain a tool-task namespace.
        assert child._checkpointer is not parent._checkpointer
        assert set(child._checkpointer.storage) == {"test"}
        assert set(parent._checkpointer.storage) == {"test"}
        assert set(child._checkpointer.storage["test"]) == {""}
        assert set(parent._checkpointer.storage["test"]) == {""}
        assert parent._state["probe"] == {}
        assert not any(message.content == "child assignment" for message in parent_model.seen[-1])
        assert len(child_model.seen) == (3 if fail_continuation else 2)
    finally:
        parent.close()
