"""DeepSeek chat completions with the project's canonical cache usage field."""

import openai
from langchain_core.messages import AIMessage
from langchain_core.messages.ai import InputTokenDetails
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI


class ChatDeepSeek(ChatOpenAI):
    """Report cache usage as cached_tokens for downstream cost accounting."""

    def _create_chat_result(
        self, response: dict | openai.BaseModel, generation_info: dict | None = None
    ) -> ChatResult:
        # Both non-streaming sync and async ChatOpenAI calls use this boundary.
        result = super()._create_chat_result(response, generation_info)
        payload = response if isinstance(response, dict) else response.model_dump()
        native_usage = payload.get("usage") or {}
        for generation in result.generations:
            message = generation.message
            if not isinstance(message, AIMessage) or message.usage_metadata is None:
                continue
            usage = message.usage_metadata
            details = dict(usage.get("input_token_details", {}))
            cache_read = details.pop("cache_read", None)
            if "cached_tokens" not in details:
                cached = cache_read if cache_read is not None else native_usage.get("prompt_cache_hit_tokens")
                if cached is not None:
                    details["cached_tokens"] = cached
            message.usage_metadata = {**usage, "input_token_details": InputTokenDetails(**details)}
        return result
