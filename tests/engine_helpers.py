"""Real graphs with deterministic models for engine integration tests."""

from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult

from src.conductor.engine import ConductorEngine
from src.agent_runtime.engine import AgentEngine


class Model(BaseChatModel):
    replies: list[Any]
    seen: list[Any] = []
    on_call: Any = None

    @property
    def _llm_type(self):
        return "engine-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        index = len(self.seen)
        self.seen.append(messages)
        if self.on_call:
            self.on_call(index)
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=self.replies[min(index, len(self.replies) - 1)].model_copy(
                        update={"id": uuid4().hex}
                    )
                )
            ]
        )


class ScriptedEngine(ConductorEngine):
    def __init__(
        self,
        *,
        model,
        tools=(),
        middleware=(),
        initial_messages=(),
        session_id="test",
        gateway_id=None,
        structured=False,
        max_iterations=1000,
        name="test",
        system_prompt="base",
    ):
        if gateway_id is None:
            from gateway_helpers import GatewayHandle, RecordingChannel
            self.test_channel = RecordingChannel("test")
            gateway_id = GatewayHandle(self.test_channel).gateway_id
        self.initial_messages = list(initial_messages)
        self._test_tools = tools
        self._test_middlewares = middleware
        self._structured = structured
        self._test_name = name
        self._test_prompt = system_prompt
        AgentEngine.__init__(
            self,
            model=model,
            gateway_id=gateway_id,
            session_id=session_id,
            max_iterations=max_iterations,
        )

    def _create_initial_messages(self):
        return self.initial_messages

    def _name(self):
        return self._test_name

    def _system_prompt(self):
        return self._test_prompt

    def _create_tools(self):
        from src.tools.communication.tools import create_talk_tool
        return [*self._test_tools, create_talk_tool(self.send_message)]

    def _strategy_registrations(self):
        return ()

    def _create_middlewares(self):
        return self._test_middlewares

    def _evaluate_result(self, state):
        if self._structured:
            return super()._evaluate_result(state)
        return AgentEngine._evaluate_result(self, state)

    def _resume_payload(self, kind, user_input):
        return AgentEngine._resume_payload(self, kind, user_input)


def run_engine(engine, messages, *, session_id="test"):
    """Seed offline input in a test fixture, then exercise the public entry point."""
    engine.initial_messages = list(messages)
    engine._execution.status = None
    engine._checkpoint_id = session_id
    return engine.run_turn()
