from __future__ import annotations

import asyncio
import builtins
import concurrent.futures
import hashlib
import json
import logging
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid7

import websockets
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import InputTokenDetails, OutputTokenDetails, UsageMetadata
from langchain_core.messages.tool import ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import (
    convert_to_openai_tool,  # pyright: ignore[reportUnknownVariableType]
)
from pydantic import BaseModel, Field, PrivateAttr
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close
from websockets.protocol import State

from src.agent_messages.text import (
    message_additional_kwargs,
    message_content,
    message_response_metadata,
)
from src.common.utils.json import JsonObject, JsonValue, json_object, json_value, load_json
from src.common.utils.validation import is_string_mapping
from src.configuration.errors import ConfigurationError, ConfigurationKeyError
from src.configuration.manager import ConfigurationManager
from src.configuration.types import ConfigValue
from src.providers.codex.protocol_types import (
    CodexMessageMetadata,
    CodexRequestOptions,
    CreateResponseRequest,
    CustomCallInput,
    FunctionCallInput,
    FunctionOutput,
    InputContent,
    ReasoningConfig,
    ReasoningInput,
    ResponseEvent,
    ResponseInput,
    ResponseItem,
    ResponseMetadata,
    ResponsePayload,
    ResponseText,
    ResponseUsage,
    UsageSummary,
    read_codex_message_metadata,
    read_request_options,
    read_response_event,
    read_response_usage,
)

DEFAULT_CODEX_WS_URL: Final[str] = "wss://chatgpt.com/backend-api/codex/responses"

logger: logging.Logger = logging.getLogger(__name__)


def _codex_setting(name: str) -> str | None:
    try:
        value: ConfigValue = ConfigurationManager.instance().get_other("codex", name)
    except ConfigurationKeyError:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("Codex connection settings must be nonempty strings.")
    return value


class CodexOAuthTokenProvider:
    """Loads Codex login credentials the same way as ws_probe_codex_login.sh."""

    def __init__(self) -> None:
        self.auth_path: Path = self._default_auth_path().expanduser()

    def _default_auth_path(self) -> Path:
        auth_json: str | None = _codex_setting("auth_json")
        if auth_json:
            return Path(auth_json)
        codex_home: Path = Path(_codex_setting("home") or "~/.codex").expanduser()
        return codex_home / "auth.json"

    def get_credentials(self) -> tuple[str, str]:
        if not self.auth_path.exists():
            raise RuntimeError(
                f"Codex auth file not found: {self.auth_path}. "
                "Run `codex login` first, or set AUTH_JSON to a file-based Codex auth state."
            )

        data: JsonValue = load_json(self.auth_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid Codex auth file: {self.auth_path}. Expected a JSON object.")
        tokens: JsonValue = data.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        token: JsonValue = tokens.get("access_token") or data.get("OPENAI_API_KEY")
        if not token:
            raise RuntimeError(
                f"Token not found in {self.auth_path}. Expected `.tokens.access_token` "
                "or `.OPENAI_API_KEY`, matching ws_probe_codex_login.sh."
            )
        account_id: str | int | float | list[JsonValue] | dict[str, JsonValue] | bool = (
            tokens.get("account_id") or ""
        )
        if not isinstance(token, str) or not isinstance(account_id, str):
            raise RuntimeError("Codex access token and account id must be strings")
        return token, account_id

    def get_token(self) -> str:
        token, _account_id = self.get_credentials()
        return token

    def get_account_id(self) -> str:
        _token, account_id = self.get_credentials()
        return account_id


class OpenAICodexModel(BaseChatModel):
    """LangChain chat model backed by Codex's ChatGPT WebSocket endpoint."""

    model: str = "gpt-5.1"
    ws_url: str = DEFAULT_CODEX_WS_URL
    wait_seconds: float = 600.0
    connect_timeout_seconds: float = 20.0
    max_connection_age_seconds: float = 55.0 * 60.0
    stream_max_retries: int = Field(default=5, ge=0)
    instructions: str = "You are a helpful assistant."
    reasoning_effort: str | None = "medium"
    store_responses: bool = False
    session_id: str = Field(default_factory=lambda: str(uuid7()))
    token_provider: CodexOAuthTokenProvider = Field(default_factory=CodexOAuthTokenProvider)
    _usage_records: list[ResponseUsage] = PrivateAttr(default_factory=list[ResponseUsage])
    _usage_totals: dict[str, int] = PrivateAttr(default_factory=dict)
    _ws: ClientConnection | None = PrivateAttr(default=None)
    _request_lock: asyncio.Lock | None = PrivateAttr(default=None)
    _connected_at: float | None = PrivateAttr(default=None)
    _connection_response_id: str | None = PrivateAttr(default=None)
    _response_stored: bool = PrivateAttr(default=False)
    _sent_messages: Counter[str] = PrivateAttr(default_factory=Counter)
    _transport_loop: asyncio.AbstractEventLoop | None = PrivateAttr(default=None)
    _transport_thread: threading.Thread | None = PrivateAttr(default=None)
    _transport_guard: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _transport_closing: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self) -> str:
        return "codex-websocket"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        message: AIMessage = self._submit_request(messages, **kwargs).result()
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        message: AIMessage = await asyncio.wrap_future(self._submit_request(messages, **kwargs))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(
        self,
        tools: Sequence[builtins.dict[str, Any] | type[BaseModel] | Callable[..., Any] | BaseTool],
        *,
        tool_choice: builtins.dict[str, Any] | str | bool | None = None,
        strict: bool | None = None,
        parallel_tool_calls: bool | None = None,
        **kwargs: object,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Bind LangChain tools using OpenAI-compatible tool schemas.

        LangChain agents call this method to attach tool definitions to a chat
        model. The Codex WebSocket endpoint uses the Responses API shape, so
        tools are converted once here and later injected into ``response.create``.
        """

        formatted_tools: list[JsonObject] = [
            _to_responses_tool(json_object(convert_to_openai_tool(tool, strict=strict))) for tool in tools
        ]
        bind_kwargs: dict[str, object] = {key: value for key, value in kwargs.items() if value is not None}
        bind_kwargs["tools"] = formatted_tools
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = _normalize_tool_choice(
                json_object(tool_choice) if isinstance(tool_choice, dict) else tool_choice
            )
        if parallel_tool_calls is not None:
            bind_kwargs["parallel_tool_calls"] = parallel_tool_calls
        return self.bind(**bind_kwargs)

    def _submit_request(
        self,
        messages: list[BaseMessage],
        **kwargs: object,
    ) -> concurrent.futures.Future[AIMessage]:
        loop: asyncio.AbstractEventLoop | None = self._ensure_transport_started()
        return asyncio.run_coroutine_threadsafe(self._request(messages, **kwargs), loop)

    def _ensure_transport_started(self) -> asyncio.AbstractEventLoop:
        with self._transport_guard:
            if self._transport_closing:
                raise RuntimeError("Codex WebSocket transport is closing.")
            if (
                self._transport_loop is not None
                and not self._transport_loop.is_closed()
                and self._transport_thread is not None
                and self._transport_thread.is_alive()
            ):
                return self._transport_loop

            loop: asyncio.AbstractEventLoop | None = asyncio.new_event_loop()
            ready: threading.Event = threading.Event()

            def run_transport_loop() -> None:
                asyncio.set_event_loop(loop)
                self._request_lock = asyncio.Lock()
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    pending: set[asyncio.Task[object]] = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()

            thread: threading.Thread | None = threading.Thread(
                target=run_transport_loop,
                name=f"codex-websocket-{id(self)}",
                daemon=True,
            )
            self._transport_loop = loop
            self._transport_thread = thread
            thread.start()
            ready.wait()
            return loop

    async def _request(self, messages: list[BaseMessage], **kwargs: object) -> AIMessage:
        if self._request_lock is None:
            raise RuntimeError("Codex WebSocket transport is not initialized.")
        request_id: str = str(uuid7())

        async with self._request_lock:
            # Retry a failed send once. Rebuild after reconnecting because an
            # unstored previous response belongs to the old connection.
            ws: ClientConnection | None = None
            for attempt in range(2):
                try:
                    ws = await self._ensure_connection()
                except Exception as error:
                    self._log_transport_failure(request_id, "connect", error)
                    raise
                request: CreateResponseRequest = self._build_request_for_connection(messages, kwargs)
                payload: str = json.dumps(request, ensure_ascii=False)
                _write_protocol_log("request", request)
                try:
                    await ws.send(payload)
                except asyncio.CancelledError:
                    await self._invalidate_connection()
                    raise
                except Exception as error:
                    self._log_transport_failure(request_id, "send", error)
                    await self._invalidate_connection()
                    if attempt == 1:
                        raise
                else:
                    break

            if ws is None:
                raise RuntimeError("Codex connection was not established")
            try:
                message: AIMessage = await self._read_response(ws)
            except asyncio.CancelledError:
                await self._invalidate_connection()
                raise
            except Exception as error:
                if not isinstance(error, RuntimeError):
                    self._log_transport_failure(request_id, "receive", error)
                await self._invalidate_connection()
                raise

            self._record_usage(message)
            self._record_response(messages, message)
            return message

    def _build_request_for_connection(
        self,
        messages: list[BaseMessage],
        kwargs: Mapping[str, object],
    ) -> CreateResponseRequest:
        options: CodexRequestOptions = read_request_options(kwargs)
        if "previous_response_id" in options:
            # Explicit continuation callers supply their own incremental input.
            return self._build_request(messages, **options)
        if self._connection_response_id is None:
            return self._build_request(messages, **options)
        return self._build_request(
            messages, previous_response_id=self._connection_response_id,
            sent_messages=self._sent_messages, **options
        )

    def _record_response(self, messages: Sequence[BaseMessage], message: AIMessage) -> None:
        """Commit the continuation cursor only after a complete response."""
        self._connection_response_id = _message_response_id(message)
        self._response_stored = message.response_metadata.get("response_store") is True
        if self._connection_response_id is not None and message.id is None:
            message.id = f"codex:{self._connection_response_id}"
        self._sent_messages |= Counter(_message_key(item) for item in messages)
        self._sent_messages[_message_key(message)] += 1

    async def _ensure_connection(self) -> ClientConnection:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        if (
            self._ws is not None
            and _connection_is_open(self._ws)
            and self._connected_at is not None
            and loop.time() - self._connected_at < self._connection_reuse_seconds()
        ):
            return self._ws

        await self._invalidate_connection()
        token, account_id = self.token_provider.get_credentials()
        sid: str = self.session_id
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "OpenAI-Beta": "responses_websockets=2026-02-06",
            "originator": "codex_cli_rs",
            "session_id": sid,
            "x-client-request-id": sid,
        }
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id

        self._ws = await websockets.connect(
            self.ws_url,
            additional_headers=headers,
            open_timeout=self.connect_timeout_seconds,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=60,
            max_size=16 * 1024 * 1024,
        )
        self._connected_at = loop.time()
        return self._ws

    def _connection_reuse_seconds(self) -> float:
        return max(0.0, self.max_connection_age_seconds - self.wait_seconds)

    def _connection_age_seconds(self) -> float | None:
        if self._connected_at is None:
            return None
        return max(0.0, asyncio.get_running_loop().time() - self._connected_at)

    def _log_transport_failure(
        self,
        request_id: str,
        phase: str,
        error: Exception,
    ) -> None:
        close_code: int | None = None
        close_reason: str | None = None
        if isinstance(error, ConnectionClosed):
            close_frame: Close | None = error.rcvd or error.sent
            if close_frame is not None:
                close_code = close_frame.code
                close_reason = close_frame.reason or None
        _write_protocol_log(
            "transport_error",
            {
                "request_id": request_id,
                "phase": phase,
                "connection_age_seconds": self._connection_age_seconds(),
                "error_type": type(error).__name__,
                "close_code": close_code,
                "close_reason": close_reason,
            },
        )

    async def _invalidate_connection(self) -> None:
        ws: ClientConnection | None = self._ws
        self._ws = None
        self._connected_at = None
        if not self._response_stored:
            self._connection_response_id = None
            self._sent_messages.clear()
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            pass

    def close(self) -> None:
        """Close the persistent WebSocket transport, if it is running."""

        state: tuple[asyncio.AbstractEventLoop, threading.Thread] | None = self._begin_transport_shutdown()
        if state is None:
            return
        loop, thread = state
        try:
            future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(
                self._close_connection_when_idle(), loop
            )
            future.result(timeout=max(self.wait_seconds, 5.0))
        finally:
            self._finish_transport_shutdown(loop, thread)

    async def aclose(self) -> None:
        """Asynchronously close the persistent WebSocket transport."""

        state: tuple[asyncio.AbstractEventLoop, threading.Thread] | None = self._begin_transport_shutdown()
        if state is None:
            return
        loop, thread = state
        try:
            future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(
                self._close_connection_when_idle(), loop
            )
            await asyncio.wrap_future(future)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            await asyncio.to_thread(thread.join)
            self._clear_transport_state(loop, thread)

    def _begin_transport_shutdown(
        self,
    ) -> tuple[asyncio.AbstractEventLoop, threading.Thread] | None:
        with self._transport_guard:
            if self._transport_closing:
                return None
            loop: asyncio.AbstractEventLoop | None = self._transport_loop
            thread: threading.Thread | None = self._transport_thread
            if loop is None or loop.is_closed() or thread is None or not thread.is_alive():
                self._transport_loop = None
                self._transport_thread = None
                self._request_lock = None
                return None
            if threading.current_thread() is thread:
                raise RuntimeError("Cannot synchronously close Codex transport from its event-loop thread.")
            self._transport_closing = True
            return loop, thread

    async def _close_connection_when_idle(self) -> None:
        if self._request_lock is None:
            await self._invalidate_connection()
            return
        async with self._request_lock:
            await self._invalidate_connection()

    def _finish_transport_shutdown(
        self,
        loop: asyncio.AbstractEventLoop,
        thread: threading.Thread,
    ) -> None:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        self._clear_transport_state(loop, thread)

    def _clear_transport_state(
        self,
        loop: asyncio.AbstractEventLoop,
        thread: threading.Thread,
    ) -> None:
        with self._transport_guard:
            if self._transport_loop is loop:
                self._transport_loop = None
            if self._transport_thread is thread:
                self._transport_thread = None
            self._request_lock = None
            self._transport_closing = False

    def get_usage_summary(self) -> UsageSummary:
        """Return cumulative token usage observed by this model instance."""

        return {
            "responses": len(self._usage_records),
            "totals": dict(self._usage_totals),
            "records": [record.copy() for record in self._usage_records],
        }

    def reset_usage(self) -> None:
        """Clear cumulative token usage counters."""

        self._usage_records.clear()
        self._usage_totals.clear()

    def _build_request(
        self,
        messages: list[BaseMessage],
        *,
        tools: Sequence[Mapping[str, JsonValue]] | None = None,
        tool_choice: JsonObject | str | bool | None = None,
        parallel_tool_calls: bool = True,
        previous_response_id: str | None = None,
        store: bool | None = None,
        sent_messages: Mapping[str, int] | None = None,
        **kwargs: object,
    ) -> CreateResponseRequest:
        instructions: str = self.instructions
        leading_system_count: int = 0
        for message in messages:
            if not isinstance(message, SystemMessage):
                break
            instructions = f"{instructions}\n\n{self._message_text(message)}"
            leading_system_count += 1

        should_store: bool = self.store_responses if store is None else bool(store)
        if previous_response_id is not None:
            previous_response_id = str(previous_response_id).strip() or None
        input_start: int = leading_system_count

        input_messages: list[ResponseInput] = []
        remaining = Counter(sent_messages or {})
        for message in messages[input_start:]:
            key = _message_key(message)
            if remaining[key]:
                remaining[key] -= 1
                continue
            if isinstance(message, SystemMessage):
                input_messages.append(
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": self._message_text(message)}],
                    }
                )
                continue
            if isinstance(message, ToolMessage):
                custom_output: FunctionOutput | None = _custom_tool_output(message)
                if custom_output is not None:
                    input_messages.append(custom_output)
                else:
                    input_messages.append(
                        {
                            "type": "function_call_output",
                            "call_id": str(message.tool_call_id or ""),
                            "output": _to_responses_input_content(message)
                            if isinstance(message_content(message), list)
                            else self._message_text(message),
                        }
                    )
                continue
            if isinstance(message, AIMessage):
                codex_metadata: CodexMessageMetadata = read_codex_message_metadata(
                    message_additional_kwargs(message)
                )
                for reasoning_item in codex_metadata.get("reasoning_items", []):
                    input_messages.append(reasoning_item)
                content: str = self._message_text(message)
                if content:
                    input_messages.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    )
                custom_call_ids: set[str] = set(codex_metadata.get("custom_tool_call_ids", []))
                for tool_call in message.tool_calls:
                    call_id: str = _tool_call_id(tool_call)
                    if call_id in custom_call_ids:
                        input_messages.append(_to_responses_custom_tool_call(tool_call))
                    else:
                        input_messages.append(_to_responses_function_call(tool_call))
                continue
            input_messages.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": _to_responses_input_content(message),
                }
            )

        reasoning: ReasoningConfig | None = (
            {
                "effort": self.reasoning_effort,
                "summary": "detailed" if self.reasoning_effort else "auto",
            }
            if self.reasoning_effort is not None
            else None
        )

        request: CreateResponseRequest = {
            "type": "response.create",
            "model": self.model,
            "instructions": instructions,
            "input": input_messages,
            "tools": [_to_responses_tool(tool) for tool in tools or []],
            "tool_choice": _normalize_tool_choice(tool_choice) if tool_choice is not None else "auto",
            "parallel_tool_calls": parallel_tool_calls,
            "reasoning": reasoning,
            "store": should_store,
            "include": ["reasoning.encrypted_content"] if reasoning else [],
            "prompt_cache_key": self.session_id,
        }
        if previous_response_id is not None:
            request["previous_response_id"] = previous_response_id
            request["prompt_cache_options"] = {"comparison_response_id": previous_response_id, "ttl": "30ms"}
        return request

    def _message_text(self, message: BaseMessage) -> str:
        content: object = message_content(message)
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False)

    async def _read_response(self, ws: ClientConnection) -> AIMessage:
        text_parts: dict[tuple[int, int], list[str]] = {}
        output_items: dict[str, ResponseItem] = {}
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        deadline: float = loop.time() + self.wait_seconds
        while True:
            remaining: float = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("No response.completed event received from Codex WebSocket.")
            message: str | bytes = await asyncio.wait_for(ws.recv(), timeout=remaining)
            if isinstance(message, bytes):
                continue
            data: ResponseEvent = read_response_event(message)
            event_type: str | None = data.get("type")
            if event_type in {"response.output_text.delta", "response.text.delta"}:
                text_key: tuple[int, int] = (data.get("output_index", 0), data.get("content_index", 0))
                text_parts.setdefault(text_key, []).append(str(data.get("delta", "")))
            elif event_type in {"response.output_text.done", "response.text.done"}:
                completed_text: str | None = data.get("text")
                if completed_text is not None:
                    text_key = (data.get("output_index", 0), data.get("content_index", 0))
                    text_parts[text_key] = [completed_text]
            elif event_type in {"response.output_item.added", "response.output_item.done"}:
                item: ResponseItem | None = data.get("item")
                if item is not None:
                    item_key: str = (
                        f"index:{data['output_index']}"
                        if "output_index" in data
                        else _response_item_key(item, len(output_items))
                    )
                    output_items[item_key] = item
            elif event_type in {"response.completed", "response.done"}:
                _write_protocol_log("response", data)
                return self._completed_message(
                    data,
                    fallback_text="".join(chunk for key in sorted(text_parts) for chunk in text_parts[key]),
                    fallback_items=list(output_items.values()),
                )
            elif event_type in {"response.failed", "response.incomplete"}:
                raise RuntimeError(f"Codex WebSocket response failed: {data}")
            elif event_type == "error":
                raise RuntimeError(f"Codex WebSocket error: {data}")

    def _completed_message(
        self,
        data: ResponseEvent,
        fallback_text: str = "",
        *,
        fallback_items: Sequence[ResponseItem] = (),
    ) -> AIMessage:
        response: ResponsePayload | None = data.get("response")
        output: Sequence[ResponseItem] | None = response.get("output") if response is not None else None
        if output is None or not output:
            output = fallback_items
        return _message_from_response_items(
            output,
            fallback_text=fallback_text,
            usage=_response_usage(data),
            response_id=_response_id(data),
            response_store=_response_store(data),
        )

    def _record_usage(self, message: AIMessage) -> None:
        raw_usage: object = message_response_metadata(message).get("usage")
        if not is_string_mapping(raw_usage):
            return
        usage: ResponseUsage = read_response_usage(raw_usage)
        self._usage_records.append(usage.copy())
        _add_usage_total(self._usage_totals, "input_tokens", usage.get("input_tokens"))
        _add_usage_total(self._usage_totals, "output_tokens", usage.get("output_tokens"))
        _add_usage_total(self._usage_totals, "total_tokens", usage.get("total_tokens"))
        input_details: dict[str, int] | None = usage.get("input_tokens_details")
        if input_details is not None:
            _add_usage_total(self._usage_totals, "cached_tokens", input_details.get("cached_tokens"))
        output_details: dict[str, int] | None = usage.get("output_tokens_details")
        if output_details is not None:
            _add_usage_total(self._usage_totals, "reasoning_tokens", output_details.get("reasoning_tokens"))


def _to_responses_input_content(message: BaseMessage) -> list[InputContent]:
    """Preserve text/image blocks instead of serializing image bytes as text."""
    content: JsonValue = json_value(message_content(message))
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]

    if not isinstance(content, list):
        raise ValueError("Message content must be text or a list of blocks")
    result: list[InputContent] = []
    for block in content:
        if isinstance(block, str):
            result.append({"type": "input_text", "text": block})
            continue
        if not isinstance(block, dict):
            raise ValueError("Message content blocks must be strings or objects")
        kind: JsonValue = block.get("type")
        if kind in ("text", "input_text"):
            text: JsonValue = block.get("text")
            if not isinstance(text, str):
                raise ValueError("Text input blocks require a string 'text'")
            result.append({"type": "input_text", "text": text})
            continue
        if kind == "image_url":
            image: JsonValue = block.get("image_url")
            url: JsonValue = image.get("url") if isinstance(image, dict) else image
            detail: JsonValue = image.get("detail") if isinstance(image, dict) else None
            if not isinstance(url, str) or not url:
                raise ValueError("Image input blocks require a non-empty image URL")
            converted: InputContent = {"type": "input_image", "image_url": url}
        elif kind == "input_image":
            converted = {"type": "input_image"}
            for key in ("image_url", "file_id"):
                value: JsonValue = block.get(key)
                if value is not None:
                    if not isinstance(value, str) or not value:
                        raise ValueError(f"Image input '{key}' must be a non-empty string")
                    converted[key] = value
            if len(converted) != 2:
                raise ValueError("Image input requires exactly one of 'image_url' or 'file_id'")
            detail = block.get("detail")
        else:
            raise ValueError(f"Unsupported user input content block type: {kind!r}")
        if detail is not None:
            if not isinstance(detail, str):
                raise ValueError("Image input 'detail' must be a string")
            converted["detail"] = detail
        result.append(converted)
    return result


def _to_responses_tool(tool: Mapping[str, JsonValue]) -> JsonObject:
    """Convert OpenAI chat-completions tool schema to Responses API schema."""

    function: JsonValue = tool.get("function")
    if tool.get("type") == "function" and isinstance(function, dict):
        result: JsonObject = {
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}}),
        }
        if "strict" in function:
            result["strict"] = function["strict"]
        return result
    return dict(tool)


def _write_protocol_log(kind: str, data: Mapping[str, object]) -> None:
    log_dir: Path = Path("workspace/logs/codex_responses")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp: str = datetime.now(UTC).isoformat()
        with (log_dir / f"codex_{timestamp}_{kind}.json").open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
    except OSError:
        # A diagnostic write failure must not trigger a paid model-call retry.
        logger.warning("Unable to write Codex protocol log", exc_info=True)


def _response_item_key(item: ResponseItem, fallback_index: int) -> str:
    return str(item.get("id") or item.get("call_id") or item.get("item_id") or f"item_{fallback_index}")


def _connection_is_open(ws: ClientConnection | None) -> bool:
    return ws is not None and ws.state is State.OPEN


def _message_from_response_items(
    items: Sequence[ResponseItem],
    fallback_text: str = "",
    usage: ResponseUsage | None = None,
    response_id: str | None = None,
    response_store: bool | None = None,
) -> AIMessage:
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    reasoning_items: list[ReasoningInput] = []
    custom_tool_call_ids: list[str] = []
    for item in items:
        if item.get("type") == "reasoning":
            reasoning_item: ReasoningInput | None = _reasoning_input_item(item)
            if reasoning_item is not None:
                reasoning_items.append(reasoning_item)
            continue
        if item.get("type") == "function_call":
            tool_calls.append(_parse_responses_function_call(item))
            continue
        if item.get("type") == "custom_tool_call":
            tool_call: ToolCall = _parse_responses_custom_tool_call(item)
            tool_calls.append(tool_call)
            custom_call_id: str | None = tool_call["id"]
            if custom_call_id is not None:
                custom_tool_call_ids.append(custom_call_id)
            continue
        texts.extend(_response_item_texts(item))
    metadata: ResponseMetadata = _response_metadata(
        usage,
        response_id=response_id,
        response_store=response_store,
    )
    return AIMessage(
        content="".join(texts) or fallback_text,
        tool_calls=tool_calls,
        additional_kwargs={
            **({"reasoning_items": reasoning_items} if reasoning_items else {}),
            **({"custom_tool_call_ids": custom_tool_call_ids} if custom_tool_call_ids else {}),
        },
        response_metadata=metadata,
        usage_metadata=_usage_metadata(usage),
    )


def _reasoning_input_item(item: ResponseItem) -> ReasoningInput | None:
    encrypted_content: str | None = item.get("encrypted_content")
    if not isinstance(encrypted_content, str) or not encrypted_content:
        return None
    result: ReasoningInput = {
        "type": "reasoning",
        "summary": list(item.get("summary") or []),
        "encrypted_content": encrypted_content,
    }
    content: list[ResponseText] | None = item.get("content")
    if content is not None and content:
        result["content"] = list(content)
    return result


def _response_item_texts(item: ResponseItem) -> list[str]:
    texts: list[str] = []
    output_text: str | None = item.get("output_text")
    if output_text is not None:
        texts.append(output_text)
    text: str | None = item.get("text")
    if text is not None and item.get("type") in {"message", "output_text"}:
        texts.append(text)
    for content_item in item.get("content", []):
        block_text: str | None = content_item.get("text")
        if block_text is not None:
            texts.append(block_text)
    return texts


def _response_usage(data: ResponseEvent) -> ResponseUsage | None:
    response: ResponsePayload | None = data.get("response")
    if response is None:
        return None
    usage: ResponseUsage | None = response.get("usage")
    return usage


def _response_id(data: ResponseEvent) -> str | None:
    response: ResponsePayload | None = data.get("response")
    if response is None:
        return None
    response_id: str | None = response.get("id")
    if not isinstance(response_id, str) or not response_id.strip():
        return None
    return response_id


def _response_store(data: ResponseEvent) -> bool | None:
    response: ResponsePayload | None = data.get("response")
    if response is None:
        return None
    store: bool | None = response.get("store")
    return store if isinstance(store, bool) else None


def _response_metadata(
    usage: ResponseUsage | None,
    *,
    response_id: str | None = None,
    response_store: bool | None = None,
) -> ResponseMetadata:
    metadata: ResponseMetadata = {}
    if usage:
        usage_dict: ResponseUsage = usage.copy()
        metadata.update(
            {
                "usage": usage_dict,
                "token_usage": usage_dict,
            }
        )
    if response_id is not None:
        metadata["response_id"] = response_id
    if response_store is not None:
        metadata["response_store"] = response_store
    return metadata


def _message_key(message: BaseMessage) -> str:
    """Keep only compact identities, never retain a second copy of image history."""
    # create_agent annotates model replies with the agent name after receipt.
    # That local label isn't serialized to Responses input and must not replay it.
    payload = message.model_dump_json(exclude={"name", "response_metadata", "usage_metadata", "artifact"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _message_response_id(message: BaseMessage) -> str | None:
    if not isinstance(message, AIMessage):
        return None
    metadata: Mapping[str, object] = message_response_metadata(message)
    response_id: object | None = metadata.get("response_id")
    if not isinstance(response_id, str) or not response_id.strip():
        return None
    return response_id


def _usage_metadata(usage: ResponseUsage | None) -> UsageMetadata | None:
    if not usage:
        return None
    metadata: UsageMetadata = {
        "input_tokens": _int_or_zero(usage.get("input_tokens")),
        "output_tokens": _int_or_zero(usage.get("output_tokens")),
        "total_tokens": _int_or_zero(usage.get("total_tokens")),
    }
    input_details: dict[str, int] | None = usage.get("input_tokens_details")
    if input_details is not None:
        metadata["input_token_details"] = InputTokenDetails(**input_details)
    output_details: dict[str, int] | None = usage.get("output_tokens_details")
    if output_details is not None:
        metadata["output_token_details"] = OutputTokenDetails(**output_details)
    return metadata


def _int_or_zero(value: int | None) -> int:
    return value if value is not None else 0


def _add_usage_total(totals: dict[str, int], key: str, value: int | None) -> None:
    amount: int = _int_or_zero(value)
    if amount:
        totals[key] = totals.get(key, 0) + amount


def _normalize_tool_choice(tool_choice: JsonObject | str | bool) -> JsonObject | str:
    if isinstance(tool_choice, bool):
        return "required" if tool_choice else "none"
    if isinstance(tool_choice, str):
        if tool_choice == "any":
            return "required"
        if tool_choice in {"auto", "none", "required"}:
            return tool_choice
        return {"type": "function", "name": tool_choice}
    function: JsonValue = tool_choice.get("function")
    if tool_choice.get("type") == "function" and isinstance(function, dict):
        name: JsonValue = function.get("name", "")
        if not isinstance(name, str):
            raise TypeError("Tool choice function name must be text")
        return {"type": "function", "name": name}
    return dict(tool_choice)


def _tool_call_id(tool_call: ToolCall) -> str:
    return tool_call["id"] or ""


def _to_responses_function_call(tool_call: ToolCall) -> FunctionCallInput:
    args: JsonObject = json_object(tool_call["args"])
    return {
        "type": "function_call",
        "call_id": tool_call["id"] or str(uuid7()),
        "name": tool_call["name"],
        "arguments": json.dumps(args, ensure_ascii=False),
    }


def _to_responses_custom_tool_call(tool_call: ToolCall) -> CustomCallInput:
    args: JsonObject = json_object(tool_call["args"])
    tool_input: JsonValue = args.get("__arg1", "")
    if not isinstance(tool_input, str):
        raise TypeError("Custom tool input must be a string")
    return {
        "type": "custom_tool_call",
        "call_id": tool_call["id"] or str(uuid7()),
        "name": tool_call["name"],
        "input": tool_input,
    }


def _parse_responses_function_call(item: ResponseItem) -> ToolCall:
    arguments: JsonValue = item.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = load_json(arguments) if arguments else {}
        except json.JSONDecodeError:
            arguments = {"__raw_arguments": arguments}
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        "name": str(item.get("name", "")),
        "args": arguments,
        "id": str(item.get("call_id") or item.get("id") or uuid7()),
    }


def _parse_responses_custom_tool_call(item: ResponseItem) -> ToolCall:
    return {
        "name": str(item.get("name", "")),
        "args": {"__arg1": str(item.get("input", ""))},
        "id": str(item.get("call_id") or item.get("id") or uuid7()),
    }


def _custom_tool_output(message: ToolMessage) -> FunctionOutput | None:
    content: JsonValue = json_value(message_content(message))
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "custom_tool_call_output":
            return {
                "type": "custom_tool_call_output",
                "call_id": str(message.tool_call_id or ""),
                "output": str(block.get("output") or ""),
            }
    return None


def create_chat_model(
    *,
    model: str,
    reasoning_effort: str | None = "medium",
    stream_max_retries: int = 5,
) -> OpenAICodexModel:
    return OpenAICodexModel(
        model=model,
        token_provider=CodexOAuthTokenProvider(),
        ws_url=_codex_setting("ws_url") or DEFAULT_CODEX_WS_URL,
        reasoning_effort=reasoning_effort,
        stream_max_retries=stream_max_retries,
    )
