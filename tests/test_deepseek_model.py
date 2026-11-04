"""Offline provider adaptation through the real sync and async model paths."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.chat import ChatCompletion

from src.providers.deepseek import ChatDeepSeek


def completion(usage: dict | None) -> dict:
    return {
        "id": "test-completion", "object": "chat.completion", "created": 0,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": "done",
        }}],
        "usage": usage,
    }


@pytest.mark.parametrize("usage,expected", [
    ({"prompt_tokens_details": {"cached_tokens": 7, "audio_tokens": 2}}, {"cached_tokens": 7, "audio": 2}),
    ({"prompt_cache_hit_tokens": 8}, {"cached_tokens": 8}),
    ({"prompt_cache_hit_tokens": 8, "prompt_tokens_details": {"cached_tokens": 0}}, {"cached_tokens": 0}),
    ({}, {}),
])
@pytest.mark.parametrize("typed", [False, True])
def test_usage_normalization_preserves_counts_and_response(usage: dict, expected: dict, typed: bool) -> None:
    payload = completion({"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13, **usage})
    original = deepcopy(payload)
    response = ChatCompletion.model_validate(payload) if typed else payload
    model = ChatDeepSeek(api_key="test", model="deepseek-flash", use_responses_api=False)
    result = model._create_chat_result(response)
    message = result.generations[0].message
    assert message.content == "done"
    assert message.usage_metadata == {
        "input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
        "input_token_details": expected, "output_token_details": {},
    }
    assert payload == original


@pytest.mark.parametrize("asynchronous", [False, True])
def test_invoke_normalizes_before_callbacks(asynchronous: bool) -> None:
    payload = ChatCompletion.model_validate(completion({
        "prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
        "prompt_tokens_details": {"cached_tokens": 7},
    }))
    raw = Mock()
    raw.parse.return_value = payload
    client = Mock()
    client.with_raw_response.create.return_value = raw
    async_client = Mock()
    async_client.with_raw_response.create = AsyncMock(return_value=raw)
    model = ChatDeepSeek(
        api_key="test", model="deepseek-flash", use_responses_api=False,
        client=client, async_client=async_client,
    )
    message = asyncio.run(model.ainvoke("hello")) if asynchronous else model.invoke("hello")
    assert message.usage_metadata["input_token_details"] == {"cached_tokens": 7}


def test_absent_usage_stays_absent() -> None:
    model = ChatDeepSeek(api_key="test", model="deepseek-flash")
    assert model._create_chat_result(completion(None)).generations[0].message.usage_metadata is None
