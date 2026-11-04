from __future__ import annotations

import asyncio
from websockets.protocol import State
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.providers.codex.codex_oauth import CodexOAuthTokenProvider, OpenAICodexModel
from src.providers.codex import codex_oauth
from src.agent_runtime.middleware.model_retry import (
    create_model_retry_middleware,
    is_retryable_model_transport_error,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import custom_tool
from websockets.exceptions import ConnectionClosedError
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response


def sample_lookup(city: str) -> str:
    """Look up sample information for a city."""

    return city


class CodexOAuthModelTests(unittest.TestCase):
    def test_credentials_accept_known_shapes_and_reject_non_object_auth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_path = Path(directory) / "auth.json"
            with patch("src.providers.codex.codex_oauth._codex_setting", side_effect=lambda name: str(auth_path) if name == "auth_json" else None):
                provider = CodexOAuthTokenProvider()
            for data, expected in [
                ({"tokens": {"access_token": "test-token", "account_id": "test-account"}}, ("test-token", "test-account")),
                ({"tokens": None, "OPENAI_API_KEY": "test-key"}, ("test-key", "")),
            ]:
                with self.subTest(data=data):
                    auth_path.write_text(json.dumps(data), encoding="utf-8")
                    self.assertEqual(provider.get_credentials(), expected)
            auth_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Expected a JSON object"):
                provider.get_credentials()

    def test_screenshot_tool_output_is_multimodal_not_encoded_text(self):
        url = "data:image/png;base64,aGVsbG8="
        message = ToolMessage(tool_call_id="screen", content=[
            {"type": "text", "text": "Frozen snapshot"},
            {"type": "image_url", "image_url": {"url": url}},
        ])
        request = OpenAICodexModel(model="test-model")._build_request([message])
        self.assertEqual(request["input"][0]["output"], [
            {"type": "input_text", "text": "Frozen snapshot"},
            {"type": "input_image", "image_url": url},
        ])

    def test_build_request_preserves_multimodal_user_content_order(self) -> None:
        model = OpenAICodexModel(model="test-model")
        data_url = "data:image/png;base64," + "aGVsbG8=" * 1000
        content = [
            {"type": "text", "text": "采集全部商家"},
            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            "第二张图",
            {"type": "image_url", "image_url": {"url": "https://example.com/page.png"}},
            {"type": "input_text", "text": "保留全部基本信息"},
        ]
        message = HumanMessage(content=content)
        request = model._build_request([SystemMessage(content="system"), message])
        blocks = request["input"][0]["content"]
        self.assertEqual(blocks, [
            {"type": "input_text", "text": "采集全部商家"},
            {"type": "input_image", "image_url": data_url, "detail": "high"},
            {"type": "input_text", "text": "第二张图"},
            {"type": "input_image", "image_url": "https://example.com/page.png"},
            {"type": "input_text", "text": "保留全部基本信息"},
        ])
        self.assertTrue(all("base64" not in block.get("text", "") for block in blocks))
        self.assertEqual(message.content, content)
        self.assertNotIn("previous_response_id", request)

    def test_build_request_accepts_native_image_blocks_and_string_urls(self) -> None:
        model = OpenAICodexModel(model="test-model")
        for block, expected in [
            ({"type": "image_url", "image_url": "https://example.com/page.png"},
             {"type": "input_image", "image_url": "https://example.com/page.png"}),
            ({"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8=", "detail": "auto"},
             {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8=", "detail": "auto"}),
            ({"type": "input_image", "file_id": "file-image", "detail": "original"},
             {"type": "input_image", "file_id": "file-image", "detail": "original"}),
        ]:
            with self.subTest(block=block):
                request = model._build_request([HumanMessage(content=[block])])
                self.assertEqual(request["input"][0]["content"], [expected])

    def test_build_request_rejects_invalid_blocks_instead_of_textualizing_them(self) -> None:
        model = OpenAICodexModel(model="test-model")
        for block in [
            {"type": "image_url", "image_url": {}},
            {"type": "image_url", "image_url": {"url": ""}},
            {"type": "image_url", "image_url": {"url": "https://example.com/p.png", "detail": 1}},
            {"type": "input_image"},
            {"type": "input_image", "file_id": 1},
            {"type": "input_image", "image_url": "https://example.com/p.png", "file_id": "file-x"},
            {"type": "text", "text": 1},
            {"type": "input_audio", "input_audio": {"data": "base64"}},
        ]:
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    model._build_request([HumanMessage(content=[block])])

    def test_build_request_keeps_plain_xml_text_unchanged(self) -> None:
        text = '采集要求：商家列表\n<hierarchy>\r\n<node text="商家"/>\r\n</hierarchy>'
        request = OpenAICodexModel(model="test-model")._build_request([HumanMessage(content=text)])
        self.assertEqual(request["input"][0]["content"], [{"type": "input_text", "text": text}])

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

    def test_bind_tools_preserves_custom_tool_grammar(self) -> None:
        patch_format = {
            "type": "grammar",
            "syntax": "lark",
            "definition": 'start: "patch"',
        }

        @custom_tool(format=patch_format)
        def apply_patch(patch: str) -> str:
            """Apply a patch."""

            return patch

        model = OpenAICodexModel(model="test-model")

        bound = model.bind_tools([apply_patch])

        self.assertEqual(
            bound.kwargs["tools"],
            [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch.",
                    "format": patch_format,
                }
            ],
        )

    def test_build_request_includes_bound_tools_and_tool_choice(self) -> None:
        model = OpenAICodexModel(
            model="test-model",
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
        self.assertEqual(request["prompt_cache_key"], model.session_id)
        self.assertEqual(request["reasoning"], {"effort": "high", "summary": "detailed"})
        self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertFalse(request["store"])
        self.assertNotIn("previous_response_id", request)
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
            second_model.session_id,
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

    def test_custom_tool_call_and_output_round_trip(self) -> None:
        model = OpenAICodexModel(model="test-model", store_responses=False)
        message = model._completed_message(
            {
                "response": {
                    "output": [
                        {
                            "type": "custom_tool_call",
                            "call_id": "patch_1",
                            "name": "apply_patch",
                            "input": "*** Begin Patch\\n*** End Patch",
                        }
                    ]
                }
            }
        )

        request = model._build_request(
            [
                HumanMessage(content="Patch the prompt."),
                message,
                ToolMessage(
                    content=[
                        {
                            "type": "custom_tool_call_output",
                            "output": '{"revision": 1}',
                        }
                    ],
                    tool_call_id="patch_1",
                ),
            ]
        )

        self.assertEqual(
            message.tool_calls,
            [
                {
                    "name": "apply_patch",
                    "args": {"__arg1": "*** Begin Patch\\n*** End Patch"},
                    "id": "patch_1",
                    "type": "tool_call",
                }
            ],
        )
        self.assertEqual(
            request["input"][1],
            {
                "type": "custom_tool_call",
                "call_id": "patch_1",
                "name": "apply_patch",
                "input": "*** Begin Patch\\n*** End Patch",
            },
        )
        self.assertEqual(
            request["input"][2],
            {
                "type": "custom_tool_call_output",
                "call_id": "patch_1",
                "output": '{"revision": 1}',
            },
        )

    def test_build_request_chains_from_latest_response_with_only_new_input(self) -> None:
        model = OpenAICodexModel(model="test-model")
        previous = model._completed_message(
            {
                "response": {
                    "id": "resp_first",
                    "store": True,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "first answer"}],
                        }
                    ],
                }
            }
        )

        model._record_response([SystemMessage(content="stable system prompt"), HumanMessage(content="first question")], previous)
        request = model._build_request_for_connection(
            [
                SystemMessage(content="stable system prompt"),
                HumanMessage(content="first question"),
                previous,
                HumanMessage(content="follow-up question"),
            ], {}
        )

        self.assertEqual(request["previous_response_id"], "resp_first")
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
                    "content": [{"type": "input_text", "text": "follow-up question"}],
                }
            ],
        )

    def test_build_request_chains_tool_output_without_repeating_prior_history(self) -> None:
        model = OpenAICodexModel(model="test-model")
        previous = model._completed_message(
            {
                "response": {
                    "id": "resp_tool_call",
                    "store": True,
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "sample_lookup",
                            "arguments": '{"city": "宿州"}',
                        }
                    ],
                }
            }
        )

        model._record_response([HumanMessage(content="Call a tool.")], previous)
        request = model._build_request_for_connection(
            [
                HumanMessage(content="Call a tool."),
                previous,
                ToolMessage(content="result text", tool_call_id="call_1"),
            ], {}
        )

        self.assertEqual(request["previous_response_id"], "resp_tool_call")
        self.assertEqual(
            request["input"],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "result text",
                }
            ],
        )

    def test_build_request_chains_unstored_response_for_websocket_incremental_input(self) -> None:
        model = OpenAICodexModel(model="test-model", store_responses=False)
        previous = model._completed_message(
            {
                "response": {
                    "id": "resp_unstored",
                    "store": False,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "first answer"}],
                        }
                    ],
                }
            }
        )

        model._record_response([HumanMessage(content="first question")], previous)
        request = model._build_request_for_connection(
            [
                HumanMessage(content="first question"),
                previous,
                HumanMessage(content="follow-up question"),
            ], {}
        )

        self.assertFalse(request["store"])
        self.assertEqual(request["previous_response_id"], "resp_unstored")
        self.assertEqual(len(request["input"]), 1)
        self.assertEqual(
            request["input"][0]["content"][0]["text"],
            "follow-up question",
        )

    def test_history_metadata_cannot_create_a_continuation(self) -> None:
        model = OpenAICodexModel(model="test-model")
        first_conversation = AIMessage(
            content="first",
            response_metadata={"response_id": "resp_a", "response_store": True},
        )
        second_conversation = AIMessage(
            content="second",
            response_metadata={"response_id": "resp_b", "response_store": True},
        )

        request_a = model._build_request_for_connection(
            [HumanMessage(content="a1"), first_conversation, HumanMessage(content="a2")], {}
        )
        request_b = model._build_request_for_connection(
            [HumanMessage(content="b1"), second_conversation, HumanMessage(content="b2")], {}
        )

        self.assertNotIn("previous_response_id", request_a)
        self.assertNotIn("previous_response_id", request_b)
        self.assertEqual(len(request_a["input"]), 3)
        self.assertEqual(len(request_b["input"]), 3)

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

    def test_completed_message_exposes_response_chain_metadata(self) -> None:
        model = OpenAICodexModel(model="test-model")

        message = model._completed_message(
            {
                "response": {
                    "id": "resp_123",
                    "store": True,
                    "output": [],
                }
            }
        )

        self.assertEqual(message.response_metadata["response_id"], "resp_123")
        self.assertTrue(message.response_metadata["response_store"])

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

    def test_read_response_keeps_reasoning_only_completed_output(self) -> None:
        model = OpenAICodexModel(model="test-model")
        reasoning = {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Checking"}],
            "encrypted_content": "opaque-test-reasoning",
        }
        message = asyncio.run(model._read_response(FakeWebSocket([{
            "type": "response.completed",
            "response": {
                "id": "resp_reasoning",
                "store": False,
                "output": [reasoning],
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
        }])))

        self.assertEqual(message.additional_kwargs["reasoning_items"], [reasoning])
        self.assertEqual(message.response_metadata["response_id"], "resp_reasoning")
        self.assertEqual(message.usage_metadata["total_tokens"], 3)

    def test_read_response_combines_text_parts_without_repeating_deltas(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = asyncio.run(model._read_response(FakeWebSocket([
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "hel"},
            {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": "hello "},
            {"type": "response.output_text.done", "output_index": 1, "content_index": 0, "text": "world"},
            {"type": "response.completed", "response": {"output": []}},
        ])))

        self.assertEqual(message.content, "hello world")

    def test_read_response_updates_anonymous_items_by_output_index(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = asyncio.run(model._read_response(FakeWebSocket([
            {
                "type": "response.output_item.added", "output_index": 0,
                "item": {"type": "function_call", "name": "sample_lookup", "arguments": ""},
            },
            {
                "type": "response.output_item.done", "output_index": 0,
                "item": {
                    "type": "function_call", "name": "sample_lookup",
                    "call_id": "call_1", "arguments": '{"city":"宿州"}',
                },
            },
            {"type": "response.completed", "response": {}},
        ])))

        self.assertEqual(len(message.tool_calls), 1)
        self.assertEqual(message.tool_calls[0]["args"], {"city": "宿州"})

    def test_read_response_prefers_completed_output_over_partial_stream(self) -> None:
        model = OpenAICodexModel(model="test-model")
        message = asyncio.run(model._read_response(FakeWebSocket([
            {"type": "response.output_text.delta", "delta": "partial"},
            *completed_events("final"),
        ])))

        self.assertEqual(message.content, "final")

    def test_send_retry_stops_after_second_failure(self) -> None:
        first_ws = PersistentFakeWebSocket([], send_error=ConnectionError("first"))
        second_ws = PersistentFakeWebSocket([], send_error=ConnectionError("second"))
        model = OpenAICodexModel(model="test-model", token_provider=FakeTokenProvider())
        sockets = iter([first_ws, second_ws])

        async def connect(*args, **kwargs):
            return next(sockets)

        try:
            with patch.object(codex_oauth.websockets, "connect", side_effect=connect) as mocked:
                with self.assertRaisesRegex(ConnectionError, "second"):
                    model.invoke([HumanMessage(content="hello")])
            self.assertEqual(mocked.call_count, 2)
            self.assertTrue(first_ws.closed)
            self.assertTrue(second_ws.closed)
        finally:
            model.close()

    def test_log_io_failure_does_not_fail_or_replay_model_call(self) -> None:
        events = completed_events("done")
        events[-1]["response"]["usage"] = {
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
        }
        ws = PersistentFakeWebSocket([events])
        model = OpenAICodexModel(model="test-model", token_provider=FakeTokenProvider())

        async def connect(*args, **kwargs):
            return ws

        try:
            with (
                patch.object(codex_oauth.websockets, "connect", side_effect=connect),
                patch.object(codex_oauth.Path, "mkdir", side_effect=PermissionError("read-only")),
                self.assertLogs(codex_oauth.logger, level="WARNING"),
            ):
                message = model.invoke([HumanMessage(content="hello")])
            self.assertEqual(message.content, "done")
            self.assertEqual(len(ws.sent), 1)
            self.assertEqual(model.get_usage_summary()["responses"], 1)
        finally:
            model.close()

    def test_sync_requests_reuse_one_persistent_connection(self) -> None:
        ws = PersistentFakeWebSocket([completed_events("first"), completed_events("second")])
        provider = FakeTokenProvider()
        model = OpenAICodexModel(model="test-model", token_provider=provider)

        async def connect(*args, **kwargs):
            return ws

        try:
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
                first = model.invoke([HumanMessage(content="first")])
                second = model.invoke([HumanMessage(content="second")])

            self.assertEqual(first.content, "first")
            self.assertEqual(second.content, "second")
            self.assertEqual(mocked_connect.call_count, 1)
            self.assertEqual(len(ws.sent), 2)
            self.assertEqual(provider.calls, 1)
        finally:
            model.close()

    def test_sync_requests_chain_previous_response_on_persistent_connection(self) -> None:
        ws = PersistentFakeWebSocket(
            [
                completed_events("first", response_id="resp_first"),
                completed_events("second", response_id="resp_second"),
            ]
        )
        model = OpenAICodexModel(
            model="test-model",
            token_provider=FakeTokenProvider(),
        )

        async def connect(*args, **kwargs):
            return ws

        try:
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect):
                first = model.invoke([HumanMessage(content="first question")])
                second = model.invoke(
                    [
                        HumanMessage(content="first question"),
                        first,
                        HumanMessage(content="follow-up question"),
                    ]
                )

            first_payload = json.loads(ws.sent[0])
            second_payload = json.loads(ws.sent[1])
            self.assertNotIn("previous_response_id", first_payload)
            self.assertEqual(second_payload["previous_response_id"], "resp_first")
            self.assertEqual(len(second_payload["input"]), 1)
            self.assertEqual(
                second_payload["input"][0]["content"][0]["text"],
                "follow-up question",
            )
            self.assertEqual(first.response_metadata["response_id"], "resp_first")
            self.assertEqual(second.response_metadata["response_id"], "resp_second")
        finally:
            model.close()

    def test_continuation_survives_removal_of_response_and_old_input(self) -> None:
        ws = PersistentFakeWebSocket([
            completed_events("first", response_id="resp_first"),
            completed_events("second", response_id="resp_second"),
        ])
        model = OpenAICodexModel(model="test-model", token_provider=FakeTokenProvider())

        async def connect(*args, **kwargs):
            return ws

        try:
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect):
                model.invoke([HumanMessage(content="old", id="user-1")])
                model.invoke([
                    HumanMessage(content="old", id="user-1"),
                    SystemMessage(content="page processing record", id="record"),
                    HumanMessage(content="new", id="user-2"),
                ])
            payload = json.loads(ws.sent[1])
            self.assertEqual(payload["previous_response_id"], "resp_first")
            self.assertEqual(len(payload["input"]), 2)
            self.assertEqual(payload["input"][0]["role"], "developer")
            self.assertEqual(payload["instructions"], "You are a helpful assistant.")
        finally:
            model.close()

    def test_incremental_input_preserves_identical_new_messages_with_distinct_ids(self) -> None:
        model = OpenAICodexModel(model="test-model")
        first = HumanMessage(content="continue", id="first")
        response = AIMessage(content="ok", response_metadata={"response_id": "resp"})
        model._record_response([first], response)
        request = model._build_request_for_connection([
            first, HumanMessage(content="continue", id="second")
        ], {})
        self.assertEqual(request["previous_response_id"], "resp")
        self.assertEqual(len(request["input"]), 1)

    def test_agent_name_annotation_does_not_replay_completed_tool_call(self) -> None:
        model = OpenAICodexModel(model="test-model")
        user = HumanMessage(content="reply", id="user")
        response = AIMessage(
            content="", tool_calls=[{"name": "reply", "args": {"message": "hello"}, "id": "call-1"}],
            response_metadata={"response_id": "resp-1"},
        )
        model._record_response([user], response)
        response.name = "dialogue"
        request = model._build_request_for_connection([
            user, response, ToolMessage(content="true", tool_call_id="call-1")
        ], {})
        self.assertEqual(request["previous_response_id"], "resp-1")
        self.assertEqual(request["input"], [{
            "type": "function_call_output", "call_id": "call-1", "output": "true"
        }])

    def test_unstored_response_restarts_with_full_context_after_reconnect(self) -> None:
        first_ws = PersistentFakeWebSocket(
            [completed_events("first", response_id="resp_first", stored=False)]
        )
        second_ws = PersistentFakeWebSocket(
            [completed_events("second", response_id="resp_second", stored=False)]
        )
        sockets = iter([first_ws, second_ws])
        model = OpenAICodexModel(
            model="test-model",
            token_provider=FakeTokenProvider(),
            store_responses=False,
        )

        async def connect(*args, **kwargs):
            return next(sockets)

        try:
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect):
                first = model.invoke([HumanMessage(content="first question")])
                model._connected_at = time.monotonic() - 2800
                model.invoke(
                    [
                        HumanMessage(content="first question"),
                        first,
                        HumanMessage(content="follow-up question"),
                    ]
                )

            second_payload = json.loads(second_ws.sent[0])
            self.assertNotIn("previous_response_id", second_payload)
            self.assertEqual(len(second_payload["input"]), 3)
        finally:
            model.close()

    def test_send_reconnect_rebuilds_unstored_incremental_request_as_full_context(self) -> None:
        first_ws = PersistentFakeWebSocket(
            [
                completed_events("first", response_id="resp_first", stored=False),
                completed_events("unused", stored=False),
            ]
        )
        second_ws = PersistentFakeWebSocket(
            [completed_events("second", response_id="resp_second", stored=False)]
        )
        sockets = iter([first_ws, second_ws])
        model = OpenAICodexModel(
            model="test-model",
            token_provider=FakeTokenProvider(),
            store_responses=False,
        )

        async def connect(*args, **kwargs):
            return next(sockets)

        try:
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect):
                first = model.invoke([HumanMessage(content="first question")])
                first_ws._send_error = ConnectionError("socket dropped before send")
                model.invoke(
                    [
                        HumanMessage(content="first question"),
                        first,
                        HumanMessage(content="follow-up question"),
                    ]
                )

            second_payload = json.loads(second_ws.sent[0])
            self.assertNotIn("previous_response_id", second_payload)
            self.assertEqual(len(second_payload["input"]), 3)
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

        with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
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
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
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
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
                with self.assertRaisesRegex(ConnectionError, "read failed"):
                    model.invoke([HumanMessage(content="do not replay")])

            self.assertEqual(mocked_connect.call_count, 1)
            self.assertEqual(len(failed_ws.sent), 1)
            self.assertTrue(failed_ws.closed)
        finally:
            model.close()

    def test_model_retry_reconnects_after_receive_disconnect(self) -> None:
        failed_ws = PersistentFakeWebSocket(
            [[
                {"type": "response.output_text.delta", "delta": "discard me"},
                ConnectionClosedError(None, None, None),
            ]]
        )
        working_ws = PersistentFakeWebSocket([completed_events("recovered")])
        sockets = iter([failed_ws, working_ws])
        model = OpenAICodexModel(
            model="test-model",
            token_provider=FakeTokenProvider(),
            stream_max_retries=1,
        )

        async def connect(*args, **kwargs):
            return next(sockets)

        middleware = create_model_retry_middleware()
        try:
            with (
                patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect,
                patch("langchain.agents.middleware.model_retry.time.sleep"),
            ):
                message = middleware.wrap_model_call(
                    None,
                    lambda _request: model.invoke([HumanMessage(content="retry receive")]),
                )

            self.assertEqual(message.content, "recovered")
            self.assertEqual(mocked_connect.call_count, 2)
            self.assertEqual(len(failed_ws.sent), 1)
            self.assertEqual(len(working_ws.sent), 1)
            self.assertNotIn("discard me", message.content)
        finally:
            model.close()

    def test_codex_retry_policy_only_accepts_transient_transport_errors(self) -> None:
        self.assertTrue(is_retryable_model_transport_error(ConnectionClosedError(None, None, None)))
        self.assertTrue(is_retryable_model_transport_error(EOFError()))
        self.assertTrue(is_retryable_model_transport_error(OSError()))
        self.assertTrue(is_retryable_model_transport_error(TimeoutError()))
        self.assertFalse(is_retryable_model_transport_error(RuntimeError("response.failed")))
        self.assertFalse(is_retryable_model_transport_error(json.JSONDecodeError("bad", "", 0)))
        self.assertTrue(
            is_retryable_model_transport_error(
                InvalidStatus(Response(503, "Unavailable", Headers()))
            )
        )
        self.assertFalse(
            is_retryable_model_transport_error(
                InvalidStatus(Response(401, "Unauthorized", Headers()))
            )
        )

    def test_codex_retry_middleware_exhausts_five_retries_and_reraises(self) -> None:
        model = OpenAICodexModel(model="test-model", stream_max_retries=5)
        middleware = create_model_retry_middleware()
        attempts = 0

        def fail(_request):
            nonlocal attempts
            attempts += 1
            raise ConnectionClosedError(None, None, None)

        with (
            patch("langchain.agents.middleware.model_retry.time.sleep") as sleep,
            self.assertRaises(ConnectionClosedError),
        ):
            middleware.wrap_model_call(None, fail)

        self.assertEqual(attempts, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_codex_retry_middleware_async_path_retries(self) -> None:
        model = OpenAICodexModel(model="test-model", stream_max_retries=1)
        middleware = create_model_retry_middleware()
        attempts = 0

        async def scenario() -> str:
            async def handler(_request):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("temporary")
                return "recovered"

            with patch("langchain.agents.middleware.model_retry.asyncio.sleep"):
                return await middleware.awrap_model_call(None, handler)

        self.assertEqual(asyncio.run(scenario()), "recovered")
        self.assertEqual(attempts, 2)

    def test_connection_configuration_has_heartbeat_and_response_time_margin(self) -> None:
        ws = PersistentFakeWebSocket([completed_events("first")])
        model = OpenAICodexModel(
            model="test-model",
            token_provider=FakeTokenProvider(),
            max_connection_age_seconds=3300,
            wait_seconds=600,
        )

        async def connect(*args, **kwargs):
            return ws

        try:
            with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
                model.invoke([HumanMessage(content="first")])

            connect_kwargs = mocked_connect.call_args.kwargs
            self.assertEqual(connect_kwargs["ping_interval"], 20)
            self.assertEqual(connect_kwargs["ping_timeout"], 60)
            self.assertEqual(model._connection_reuse_seconds(), 2700)
        finally:
            model.close()

    def test_expired_connection_is_replaced_and_close_is_idempotent(self) -> None:
        first_ws = PersistentFakeWebSocket([completed_events("first")])
        second_ws = PersistentFakeWebSocket([completed_events("second")])
        sockets = iter([first_ws, second_ws])
        provider = FakeTokenProvider()
        model = OpenAICodexModel(
            model="test-model",
            token_provider=provider,
            store_responses=True,
        )

        async def connect(*args, **kwargs):
            return next(sockets)

        with patch("src.providers.codex.codex_oauth.websockets.connect", side_effect=connect) as mocked_connect:
            first = model.invoke([HumanMessage(content="first")])
            # The socket is younger than its hard lease, but too old to fit a
            # complete wait_seconds response window.
            model._connected_at = time.monotonic() - 2800
            model.invoke(
                [
                    HumanMessage(content="first"),
                    first,
                    HumanMessage(content="second"),
                ]
            )

        model.close()
        model.close()
        self.assertEqual(mocked_connect.call_count, 2)
        self.assertEqual(provider.calls, 2)
        self.assertTrue(first_ws.closed)
        self.assertTrue(second_ws.closed)
        self.assertEqual(
            json.loads(second_ws.sent[0])["previous_response_id"],
            "resp_first",
        )


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


class PersistentFakeWebSocket:
    def __init__(
        self,
        response_batches: list[list[dict | BaseException] | BaseException],
        *,
        send_error: BaseException | None = None,
        block_first_response: bool = False,
    ) -> None:
        self._response_batches = list(response_batches)
        self._current_messages: list[str | BaseException] = []
        self._send_error = send_error
        self._block_first_response = block_first_response
        self._response_number = 0
        self.sent: list[str] = []
        self.state = State.OPEN
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
            self._current_messages = [batch]
        else:
            self._current_messages = [
                event if isinstance(event, BaseException) else json.dumps(event, ensure_ascii=False)
                for event in batch
            ]
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
        self.state = State.CLOSED


def completed_events(
    text: str,
    response_id: str | None = None,
    *,
    stored: bool = True,
) -> list[dict]:
    return [
        {
            "type": "response.completed",
            "response": {
                "id": response_id or f"resp_{text}",
                "store": stored,
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
