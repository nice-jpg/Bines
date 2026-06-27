from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

import websockets
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr
import builtins
from collections.abc import Callable
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from langchain_core.language_models.base import (
    BaseLanguageModel,
    LangSmithParams,
    LanguageModelInput,
)

class CodexOAuthTokenProvider:
    """Loads Codex login credentials the same way as ws_probe_codex_login.sh."""

    ENV_KEYS = ("AUTH_JSON",)

    def __init__(self, auth_path: str | Path | None = None) -> None:
        self.auth_path = Path(auth_path or self._default_auth_path()).expanduser()

    def _default_auth_path(self) -> Path:
        auth_json = os.getenv("AUTH_JSON")
        if auth_json:
            return Path(auth_json)
        codex_home = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
        return codex_home / "auth.json"

    def get_credentials(self) -> tuple[str, str]:
        if not self.auth_path.exists():
            raise RuntimeError(
                f"Codex auth file not found: {self.auth_path}. "
                "Run `codex login` first, or set AUTH_JSON to a file-based Codex auth state."
            )

        data = json.loads(self.auth_path.read_text())
        token = self._read_path(data, ("tokens", "access_token")) or data.get("OPENAI_API_KEY")
        if not token:
            raise RuntimeError(
                f"Token not found in {self.auth_path}. Expected `.tokens.access_token` "
                "or `.OPENAI_API_KEY`, matching ws_probe_codex_login.sh."
            )
        account_id = self._read_path(data, ("tokens", "account_id")) or ""
        return str(token), str(account_id)

    def get_token(self) -> str:
        token, _account_id = self.get_credentials()
        return token

    def get_account_id(self) -> str:
        _token, account_id = self.get_credentials()
        return account_id

    def _read_path(self, value: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = value
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current


class OpenAICodexModel(BaseChatModel):
    """LangChain chat model backed by Codex's ChatGPT WebSocket endpoint."""

    model: str = "gpt-5.1"
    ws_url: str = "wss://chatgpt.com/backend-api/codex/responses"
    wait_seconds: float = 600.0
    connect_timeout_seconds: float = 20.0
    instructions: str = "You are a helpful assistant."
    token_provider: CodexOAuthTokenProvider = Field(default_factory=CodexOAuthTokenProvider)
    _usage_records: list[dict[str, Any]] = PrivateAttr(default_factory=list)
    _usage_totals: dict[str, int] = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "codex-websocket"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            message = asyncio.run(self._request(messages, **kwargs))
        else:
            raise RuntimeError("Use `ainvoke` when CodexWebSocketChatModel is called inside an event loop.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = await self._request(messages, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=message)])
    
    def bind_tools(
        self,
        tools: Sequence[builtins.dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: builtins.dict[str, Any] | str | bool | None = None,
        strict: bool | None = None,
        parallel_tool_calls: bool | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Bind LangChain tools using OpenAI-compatible tool schemas.

        LangChain agents call this method to attach tool definitions to a chat
        model. The Codex WebSocket endpoint uses the Responses API shape, so
        tools are converted once here and later injected into ``response.create``.
        """

        formatted_tools = [_to_responses_tool(convert_to_openai_tool(tool, strict=strict)) for tool in tools]
        bind_kwargs = {key: value for key, value in kwargs.items() if value is not None}
        bind_kwargs["tools"] = formatted_tools
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = _normalize_tool_choice(tool_choice)
        if parallel_tool_calls is not None:
            bind_kwargs["parallel_tool_calls"] = parallel_tool_calls
        return self.bind(**bind_kwargs)

    async def _request(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        token, account_id = self.token_provider.get_credentials()
        sid = str(uuid4())
        headers = {
            "Authorization": f"Bearer {token}",
            "OpenAI-Beta": "responses_websockets=2026-02-06",
            "originator": "codex_cli_rs",
            "session_id": sid,
            "x-client-request-id": sid,
        }
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id

        request = self._build_request(messages, **kwargs)
        async with websockets.connect(
            self.ws_url,
            additional_headers=headers,
            open_timeout=self.connect_timeout_seconds,
            close_timeout=5,
            max_size=16 * 1024 * 1024,
        ) as ws:
            await ws.send(json.dumps(request, ensure_ascii=False))
            message = await self._read_response(ws)
            self._record_usage(message)
            return message

    def get_usage_summary(self) -> dict[str, Any]:
        """Return cumulative token usage observed by this model instance."""

        return {
            "responses": len(self._usage_records),
            "totals": dict(self._usage_totals),
            "records": [dict(record) for record in self._usage_records],
        }

    def reset_usage(self) -> None:
        """Clear cumulative token usage counters."""

        self._usage_records.clear()
        self._usage_totals.clear()

    def _build_request(
        self,
        messages: list[BaseMessage],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: builtins.dict[str, Any] | str | bool | None = None,
        parallel_tool_calls: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        instructions = self.instructions
        input_messages: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                instructions = f"{instructions}\n\n{self._message_text(message)}"
                continue
            if isinstance(message, ToolMessage):
                input_messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(getattr(message, "tool_call_id", "")),
                        "output": self._message_text(message),
                    }
                )
                continue
            if isinstance(message, AIMessage):
                content = self._message_text(message)
                if content:
                    input_messages.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    )
                for tool_call in getattr(message, "tool_calls", []) or []:
                    input_messages.append(_to_responses_function_call(tool_call))
                continue
            role = "assistant" if isinstance(message, AIMessage) else "user"
            input_messages.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": self._message_text(message)}],
                }
            )

        return {
            "type": "response.create",
            "model": self.model,
            "instructions": instructions,
            "input": input_messages,
            "tools": [_to_responses_tool(tool) for tool in tools or []],
            "tool_choice": _normalize_tool_choice(tool_choice) if tool_choice is not None else "auto",
            "parallel_tool_calls": parallel_tool_calls,
            "reasoning": None,
            "store": False,
            "stream": True,
            "include": [],
        }

    def _message_text(self, message: BaseMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False)

    async def _read_response(self, ws: Any) -> AIMessage:
        chunks: list[str] = []
        output_items: dict[str, dict[str, Any]] = {}
        output_item_order: list[str] = []
        done_text = ""
        deadline = asyncio.get_running_loop().time() + self.wait_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("No response.completed event received from Codex WebSocket.")
            message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            if isinstance(message, bytes):
                continue
            data = json.loads(message)
            event_type = data.get("type")
            if event_type in {"response.output_text.delta", "response.text.delta"}:
                chunks.append(str(data.get("delta", "")))
            elif event_type in {"response.output_text.done", "response.text.done"}:
                if isinstance(data.get("text"), str):
                    done_text = data["text"]
            elif event_type in {"response.output_item.added", "response.output_item.done"}:
                item = data.get("item")
                if isinstance(item, dict):
                    key = _response_item_key(item, len(output_item_order))
                    if key not in output_items:
                        output_item_order.append(key)
                    output_items[key] = item
            elif event_type in {"response.completed", "response.done"}:
                fallback = done_text or "".join(chunks)
                usage = _response_usage(data)
                completed = self._completed_message(data, fallback_text="")
                if completed.content or completed.tool_calls:
                    return completed
                items = [output_items[key] for key in output_item_order]
                return _message_from_response_items(items, fallback_text=fallback, usage=usage)
            elif event_type in {"response.failed", "response.incomplete"}:
                raise RuntimeError(f"Codex WebSocket response failed: {data}")

    def _completed_message(self, data: dict[str, Any], fallback_text: str = "") -> AIMessage:
        response = data.get("response")
        if not isinstance(response, dict):
            return AIMessage(content=fallback_text)
        output = response.get("output")
        if not isinstance(output, list):
            return AIMessage(content=fallback_text)
        return _message_from_response_items(output, fallback_text=fallback_text, usage=_response_usage(data))

    def _completed_text(self, data: dict[str, Any]) -> str:
        return str(self._completed_message(data).content or "")

    def _record_usage(self, message: AIMessage) -> None:
        usage = message.response_metadata.get("usage") if isinstance(message.response_metadata, dict) else None
        if not isinstance(usage, Mapping):
            return

        record = dict(usage)
        self._usage_records.append(record)
        _add_usage_total(self._usage_totals, "input_tokens", usage.get("input_tokens"))
        _add_usage_total(self._usage_totals, "output_tokens", usage.get("output_tokens"))
        _add_usage_total(self._usage_totals, "total_tokens", usage.get("total_tokens"))

        input_details = usage.get("input_tokens_details")
        if isinstance(input_details, Mapping):
            _add_usage_total(self._usage_totals, "cached_tokens", input_details.get("cached_tokens"))
        output_details = usage.get("output_tokens_details")
        if isinstance(output_details, Mapping):
            _add_usage_total(self._usage_totals, "reasoning_tokens", output_details.get("reasoning_tokens"))


def _to_responses_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    """Convert OpenAI chat-completions tool schema to Responses API schema."""

    if tool.get("type") == "function" and isinstance(tool.get("function"), Mapping):
        function = tool["function"]
        result = {
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}}),
        }
        if "strict" in function:
            result["strict"] = function["strict"]
        return result
    return dict(tool)


def _response_item_key(item: Mapping[str, Any], fallback_index: int) -> str:
    return str(
        item.get("id")
        or item.get("call_id")
        or item.get("item_id")
        or f"item_{fallback_index}"
    )


def _message_from_response_items(
    items: Sequence[Any],
    fallback_text: str = "",
    usage: Mapping[str, Any] | None = None,
) -> AIMessage:
    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "function_call":
            tool_calls.append(_parse_responses_function_call(item))
            continue
        texts.extend(_response_item_texts(item))
    metadata = _response_metadata(usage)
    return AIMessage(
        content="".join(texts) or fallback_text,
        tool_calls=tool_calls,
        response_metadata=metadata,
        usage_metadata=_usage_metadata(usage),
    )


def _response_item_texts(item: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    if isinstance(item.get("output_text"), str):
        texts.append(str(item["output_text"]))
    if isinstance(item.get("text"), str) and item.get("type") in {"message", "output_text"}:
        texts.append(str(item["text"]))

    content = item.get("content")
    if isinstance(content, list):
        for content_item in content:
            if isinstance(content_item, Mapping) and isinstance(content_item.get("text"), str):
                texts.append(str(content_item["text"]))
    return texts


def _response_usage(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    response = data.get("response")
    if not isinstance(response, Mapping):
        return None
    usage = response.get("usage")
    return usage if isinstance(usage, Mapping) else None


def _response_metadata(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    if not usage:
        return {}
    usage_dict = dict(usage)
    return {
        "usage": usage_dict,
        "token_usage": usage_dict,
    }


def _usage_metadata(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not usage:
        return None
    metadata: dict[str, Any] = {
        "input_tokens": _int_or_zero(usage.get("input_tokens")),
        "output_tokens": _int_or_zero(usage.get("output_tokens")),
        "total_tokens": _int_or_zero(usage.get("total_tokens")),
    }
    input_details = usage.get("input_tokens_details")
    if isinstance(input_details, Mapping):
        metadata["input_token_details"] = dict(input_details)
    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, Mapping):
        metadata["output_token_details"] = dict(output_details)
    return metadata


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _add_usage_total(totals: dict[str, int], key: str, value: Any) -> None:
    amount = _int_or_zero(value)
    if amount:
        totals[key] = totals.get(key, 0) + amount


def _normalize_tool_choice(tool_choice: builtins.dict[str, Any] | str | bool) -> builtins.dict[str, Any] | str:
    if isinstance(tool_choice, bool):
        return "required" if tool_choice else "none"
    if isinstance(tool_choice, str):
        if tool_choice == "any":
            return "required"
        if tool_choice in {"auto", "none", "required"}:
            return tool_choice
        return {"type": "function", "name": tool_choice}
    if isinstance(tool_choice, Mapping):
        if tool_choice.get("type") == "function" and isinstance(tool_choice.get("function"), Mapping):
            return {"type": "function", "name": str(tool_choice["function"].get("name", ""))}
        return dict(tool_choice)
    return "auto"


def _to_responses_function_call(tool_call: Any) -> dict[str, Any]:
    if isinstance(tool_call, Mapping):
        call_id = tool_call.get("id") or tool_call.get("call_id")
        name = tool_call.get("name")
        args = tool_call.get("args") or tool_call.get("arguments") or {}
    else:
        call_id = getattr(tool_call, "id", None) or getattr(tool_call, "call_id", None)
        name = getattr(tool_call, "name", None)
        args = getattr(tool_call, "args", None) or getattr(tool_call, "arguments", None) or {}
    return {
        "type": "function_call",
        "call_id": str(call_id or uuid4()),
        "name": str(name or ""),
        "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
    }


def _parse_responses_function_call(item: Mapping[str, Any]) -> dict[str, Any]:
    arguments = item.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            arguments = {"__raw_arguments": arguments}
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        "name": str(item.get("name", "")),
        "args": arguments,
        "id": str(item.get("call_id") or item.get("id") or uuid4()),
    }


def create_chat_model(
    *,
    model: str,
    temperature: float = 0.0,
    token_provider: CodexOAuthTokenProvider | None = None,
    base_url: str | None = None,
) -> OpenAICodexModel:
    provider = token_provider or CodexOAuthTokenProvider()
    return OpenAICodexModel(
        model=model,
        token_provider=provider,
        ws_url=base_url or os.getenv("CODEX_WS_URL", "wss://chatgpt.com/backend-api/codex/responses"),
    )
