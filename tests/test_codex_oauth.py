from __future__ import annotations

import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex.codex_oauth import CodexOAuthTokenProvider, OpenAICodexModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool


def sample_lookup(city: str) -> str:
    """Look up sample information for a city."""

    return city


class CodexOAuthModelTests(unittest.TestCase):
    def test_bind_tools_converts_langchain_tools_to_responses_schema(self) -> None:
        tool = StructuredTool.from_function(
            func=sample_lookup,
            name="sample_lookup",
            description="Look up sample information for a city.",
        )
        model = OpenAICodexModel(model="test-model")

        bound = model.bind_tools(
            [tool],
            tool_choice="sample_lookup",
            strict=True,
            parallel_tool_calls=False,
        )

        self.assertEqual(bound.kwargs["tool_choice"], {"type": "function", "name": "sample_lookup"})
        self.assertFalse(bound.kwargs["parallel_tool_calls"])
        self.assertEqual(
            bound.kwargs["tools"][0],
            {
                "type": "function",
                "name": "sample_lookup",
                "description": "Look up sample information for a city.",
                "parameters": {
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                    "type": "object",
                    "additionalProperties": False,
                },
                "strict": True,
            },
        )

    def test_build_request_includes_bound_tools_and_tool_choice(self) -> None:
        model = OpenAICodexModel(
            model="test-model",
            prompt_cache_key="cache-key",
            reasoning_effort="high",
        )
        request = model._build_request(
            [HumanMessage(content="Use the tool.")],
            tools=[
                {
                    "type": "function",
                    "name": "sample_lookup",
                    "description": "Look up sample information for a city.",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            tool_choice={"type": "function", "name": "sample_lookup"},
            parallel_tool_calls=False,
        )

        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["tools"][0]["name"], "sample_lookup")
        self.assertEqual(request["tool_choice"], {"type": "function", "name": "sample_lookup"})
        self.assertFalse(request["parallel_tool_calls"])
        self.assertEqual(request["prompt_cache_key"], "cache-key")
        self.assertEqual(request["reasoning"], {"effort": "high"})
        self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertNotIn("stream", request)

    def test_dynamic_system_message_stays_at_end_of_input(self) -> None:
        model = OpenAICodexModel(model="test-model")

        request = model._build_request(
            [
                SystemMessage(content="stable system prompt"),
                HumanMessage(content="conversation history"),
                SystemMessage(content="dynamic trace"),
            ]
        )

        self.assertEqual(
            request["instructions"],
            "You are a helpful assistant.\n\nstable system prompt",
        )
        self.assertEqual(
            request["input"],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "conversation history"}],
                },
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "dynamic trace"}],
                },
            ],
        )

    def test_prompt_cache_key_is_stable_per_model_instance(self) -> None:
        first_model = OpenAICodexModel(model="test-model")
        second_model = OpenAICodexModel(model="test-model")

        first_request = first_model._build_request([HumanMessage(content="first")])
        second_request = first_model._build_request([HumanMessage(content="second")])

        self.assertEqual(
            first_request["prompt_cache_key"],
            second_request["prompt_cache_key"],
        )
        self.assertNotEqual(
            first_request["prompt_cache_key"],
            second_model.prompt_cache_key,
        )

    def test_build_request_serializes_tool_call_history(self) -> None:
        model = OpenAICodexModel(model="test-model")
        messages = [
            HumanMessage(content="Call a tool."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sample_lookup",
                        "args": {"city": "宿州"},
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(content="result text", tool_call_id="call_1"),
        ]

        request = model._build_request(messages)

        self.assertEqual(
            request["input"][1],
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "sample_lookup",
                "arguments": '{"city": "宿州"}',
            },
        )
        self.assertEqual(
            request["input"][2],
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "result text",
            },
        )

    def test_completed_message_parses_responses_function_calls(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = model._completed_message(
            {
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "sample_lookup",
                            "arguments": '{"city": "宿州"}',
                        }
                    ]
                }
            }
        )

        self.assertEqual(message.content, "")
        self.assertEqual(
            message.tool_calls,
            [{"name": "sample_lookup", "args": {"city": "宿州"}, "id": "call_1", "type": "tool_call"}],
        )

    def test_reasoning_state_is_preserved_for_tool_followup(self) -> None:
        model = OpenAICodexModel(model="test-model", reasoning_effort="high")
        message = model._completed_message(
            {
                "response": {
                    "output": [
                        {
                            "type": "reasoning",
                            "id": "reasoning_1",
                            "summary": [
                                {"type": "summary_text", "text": "Inspect first."}
                            ],
                            "encrypted_content": "encrypted-state",
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "sample_lookup",
                            "arguments": '{"city": "宿州"}',
                        },
                    ]
                }
            }
        )

        request = model._build_request(
            [
                HumanMessage(content="Call a tool."),
                message,
                ToolMessage(content="result", tool_call_id="call_1"),
            ]
        )

        self.assertEqual(
            request["input"][1],
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Inspect first."}],
                "encrypted_content": "encrypted-state",
            },
        )
        self.assertEqual(request["input"][2]["type"], "function_call")
        self.assertEqual(request["input"][3]["type"], "function_call_output")

    def test_completed_message_extracts_openai_response_usage(self) -> None:
        model = OpenAICodexModel(model="test-model")
        usage = {
            "input_tokens": 36,
            "input_tokens_details": {"cached_tokens": 10},
            "output_tokens": 87,
            "output_tokens_details": {"reasoning_tokens": 20},
            "total_tokens": 123,
        }

        message = model._completed_message(
            {
                "response": {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "hello"}],
                        }
                    ],
                    "usage": usage,
                }
            }
        )

        self.assertEqual(message.content, "hello")
        self.assertEqual(message.response_metadata["usage"], usage)
        self.assertEqual(message.response_metadata["token_usage"], usage)
        self.assertEqual(
            message.usage_metadata,
            {
                "input_tokens": 36,
                "output_tokens": 87,
                "total_tokens": 123,
                "input_token_details": {"cached_tokens": 10},
                "output_token_details": {"reasoning_tokens": 20},
            },
        )

    def test_model_records_cumulative_usage(self) -> None:
        model = OpenAICodexModel(model="test-model")
        first = model._completed_message(
            {
                "response": {
                    "output": [],
                    "usage": {
                        "input_tokens": 10,
                        "input_tokens_details": {"cached_tokens": 3},
                        "output_tokens": 5,
                        "output_tokens_details": {"reasoning_tokens": 2},
                        "total_tokens": 15,
                    },
                }
            }
        )
        second = model._completed_message(
            {
                "response": {
                    "output": [],
                    "usage": {
                        "input_tokens": 4,
                        "input_tokens_details": {"cached_tokens": 1},
                        "output_tokens": 6,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 10,
                    },
                }
            }
        )

        model._record_usage(first)
        model._record_usage(second)
        summary = model.get_usage_summary()

        self.assertEqual(summary["responses"], 2)
        self.assertEqual(
            summary["totals"],
            {
                "input_tokens": 14,
                "output_tokens": 11,
                "total_tokens": 25,
                "cached_tokens": 4,
                "reasoning_tokens": 2,
            },
        )
        self.assertEqual(len(summary["records"]), 2)

        model.reset_usage()
        self.assertEqual(model.get_usage_summary(), {"responses": 0, "totals": {}, "records": []})

    def test_read_response_uses_output_item_done_text_when_completed_has_no_output(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = asyncio.run(
            model._read_response(
                FakeWebSocket(
                    [
                        {
                            "type": "response.output_item.done",
                            "item": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "hello from item"}],
                            },
                        },
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_1",
                                "usage": {
                                    "input_tokens": 1,
                                    "output_tokens": 2,
                                    "total_tokens": 3,
                                },
                            },
                        },
                    ]
                )
            )
        )

        self.assertEqual(message.content, "hello from item")
        self.assertEqual(message.tool_calls, [])
        self.assertEqual(message.usage_metadata["total_tokens"], 3)

    def test_read_response_uses_output_item_done_function_call_before_response_done(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = asyncio.run(
            model._read_response(
                FakeWebSocket(
                    [
                        {
                            "type": "response.output_item.done",
                            "item": {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "sample_lookup",
                                "arguments": '{"city": "宿州"}',
                            },
                        },
                        {"type": "response.done", "response": {"usage": {}}},
                    ]
                )
            )
        )

        self.assertEqual(message.content, "")
        self.assertEqual(
            message.tool_calls,
            [{"name": "sample_lookup", "args": {"city": "宿州"}, "id": "call_1", "type": "tool_call"}],
        )

    def test_read_response_uses_text_delta_when_no_output_items_exist(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = asyncio.run(
            model._read_response(
                FakeWebSocket(
                    [
                        {"type": "response.output_text.delta", "delta": "hello "},
                        {"type": "response.output_text.delta", "delta": "delta"},
                        {"type": "response.completed", "response": {"id": "resp_1", "usage": {}}},
                    ]
                )
            )
        )

        self.assertEqual(message.content, "hello delta")

    def test_sync_requests_reuse_one_persistent_connection(self) -> None:
        ws = PersistentFakeWebSocket([completed_events("first"), completed_events("second")])
        provider = FakeTokenProvider()
        model = OpenAICodexModel(model="test-model", token_provider=provider)

        async def connect(*args, **kwargs):
            return ws

        try:
            with patch("codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
                first = model.invoke([HumanMessage(content="first")])
                second = model.invoke([HumanMessage(content="second")])

            self.assertEqual(first.content, "first")
            self.assertEqual(second.content, "second")
            self.assertEqual(mocked_connect.call_count, 1)
            self.assertEqual(len(ws.sent), 2)
            self.assertEqual(provider.calls, 1)
        finally:
            model.close()

    def test_async_requests_share_transport_and_are_serialized(self) -> None:
        ws = PersistentFakeWebSocket(
            [completed_events("first"), completed_events("second")],
            block_first_response=True,
        )
        model = OpenAICodexModel(model="test-model", token_provider=FakeTokenProvider())

        async def connect(*args, **kwargs):
            return ws

        async def scenario() -> tuple[AIMessage, AIMessage]:
            first = asyncio.create_task(model.ainvoke([HumanMessage(content="first")]))
            self.assertTrue(await asyncio.to_thread(ws.first_recv_started.wait, 2.0))
            second = asyncio.create_task(model.ainvoke([HumanMessage(content="second")]))
            await asyncio.sleep(0.05)
            self.assertEqual(len(ws.sent), 1)
            ws.release_first_response.set()
            results = await asyncio.gather(first, second)
            await model.aclose()
            return results[0], results[1]

        with patch("codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
            first, second = asyncio.run(scenario())

        self.assertEqual(first.content, "first")
        self.assertEqual(second.content, "second")
        self.assertEqual(mocked_connect.call_count, 1)
        self.assertEqual(len(ws.sent), 2)

    def test_send_failure_reconnects_and_retries_once(self) -> None:
        failed_ws = PersistentFakeWebSocket([], send_error=ConnectionError("closed"))
        working_ws = PersistentFakeWebSocket([completed_events("recovered")])
        provider = FakeTokenProvider()
        model = OpenAICodexModel(model="test-model", token_provider=provider)

        async def connect(*args, **kwargs):
            return failed_ws if provider.calls == 1 else working_ws

        try:
            with patch("codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
                message = model.invoke([HumanMessage(content="retry")])

            self.assertEqual(message.content, "recovered")
            self.assertEqual(mocked_connect.call_count, 2)
            self.assertEqual(provider.calls, 2)
            self.assertTrue(failed_ws.closed)
            self.assertEqual(len(working_ws.sent), 1)
        finally:
            model.close()

    def test_receive_failure_invalidates_connection_without_replaying(self) -> None:
        failed_ws = PersistentFakeWebSocket([ConnectionError("read failed")])
        provider = FakeTokenProvider()
        model = OpenAICodexModel(model="test-model", token_provider=provider)

        async def connect(*args, **kwargs):
            return failed_ws

        try:
            with patch("codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
                with self.assertRaisesRegex(ConnectionError, "read failed"):
                    model.invoke([HumanMessage(content="do not replay")])

            self.assertEqual(mocked_connect.call_count, 1)
            self.assertEqual(len(failed_ws.sent), 1)
            self.assertTrue(failed_ws.closed)
        finally:
            model.close()

    def test_expired_connection_is_replaced_and_close_is_idempotent(self) -> None:
        first_ws = PersistentFakeWebSocket([completed_events("first")])
        second_ws = PersistentFakeWebSocket([completed_events("second")])
        sockets = iter([first_ws, second_ws])
        provider = FakeTokenProvider()
        model = OpenAICodexModel(model="test-model", token_provider=provider)

        async def connect(*args, **kwargs):
            return next(sockets)

        with patch("codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
            model.invoke([HumanMessage(content="first")])
            model._connected_at = float("-inf")
            model.invoke([HumanMessage(content="second")])

        model.close()
        model.close()
        self.assertEqual(mocked_connect.call_count, 2)
        self.assertEqual(provider.calls, 2)
        self.assertTrue(first_ws.closed)
        self.assertTrue(second_ws.closed)


class FakeWebSocket:
    def __init__(self, events: list[dict]) -> None:
        self._messages = [json.dumps(event, ensure_ascii=False) for event in events]

    async def recv(self) -> str:
        if not self._messages:
            raise AssertionError("No fake websocket messages remain.")
        return self._messages.pop(0)


class FakeTokenProvider(CodexOAuthTokenProvider):
    def __init__(self) -> None:
        self.calls = 0

    def get_credentials(self) -> tuple[str, str]:
        self.calls += 1
        return f"token-{self.calls}", "account"


class FakeConnectionState:
    def __init__(self, name: str = "OPEN") -> None:
        self.name = name


class PersistentFakeWebSocket:
    def __init__(
        self,
        response_batches: list[list[dict] | BaseException],
        *,
        send_error: BaseException | None = None,
        block_first_response: bool = False,
    ) -> None:
        self._response_batches = list(response_batches)
        self._current_messages: list[str] = []
        self._send_error = send_error
        self._block_first_response = block_first_response
        self._response_number = 0
        self.sent: list[str] = []
        self.state = FakeConnectionState()
        self.closed = False
        self.first_recv_started = threading.Event()
        self.release_first_response = threading.Event()

    async def send(self, payload: str) -> None:
        if self._send_error is not None:
            error = self._send_error
            self._send_error = None
            raise error
        self.sent.append(payload)
        batch = self._response_batches.pop(0)
        if isinstance(batch, BaseException):
            self._current_messages = [batch]  # type: ignore[list-item]
        else:
            self._current_messages = [json.dumps(event, ensure_ascii=False) for event in batch]
        self._response_number += 1

    async def recv(self) -> str:
        if self._response_number == 1 and self._block_first_response:
            self.first_recv_started.set()
            while not self.release_first_response.is_set():
                await asyncio.sleep(0.01)
        if not self._current_messages:
            raise AssertionError("No fake websocket messages remain.")
        message = self._current_messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        return message

    async def close(self) -> None:
        self.closed = True
        self.state.name = "CLOSED"


def completed_events(text: str) -> list[dict]:
    return [
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": {},
            },
        }
    ]


if __name__ == "__main__":
    unittest.main()
