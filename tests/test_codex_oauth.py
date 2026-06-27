from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex.codex_oauth import OpenAICodexModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
        model = OpenAICodexModel(model="test-model")
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


class FakeWebSocket:
    def __init__(self, events: list[dict]) -> None:
        self._messages = [json.dumps(event, ensure_ascii=False) for event in events]

    async def recv(self) -> str:
        if not self._messages:
            raise AssertionError("No fake websocket messages remain.")
        return self._messages.pop(0)


if __name__ == "__main__":
    unittest.main()
