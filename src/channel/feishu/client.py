"""Feishu text message sending client."""

from __future__ import annotations

import json
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from .config import FeishuConfig


class FeishuMessenger:
    """Small wrapper around Feishu IM text message APIs."""

    def __init__(self, config: FeishuConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = client or lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()

    def send_text(self, chat_id: str, text: str) -> None:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(_text_content(text))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        _check_response(response, "client.im.v1.message.create")

    def reply_text(self, message_id: str, text: str) -> None:
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(_text_content(text))
                .msg_type("text")
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.reply(request)
        _check_response(response, "client.im.v1.message.reply")


def _text_content(text: str) -> str:
    return json.dumps({"text": str(text)}, ensure_ascii=False)


def _check_response(response: Any, operation: str) -> None:
    success = response.success() if hasattr(response, "success") else bool(response)
    if success:
        return
    code = getattr(response, "code", "")
    msg = getattr(response, "msg", "")
    log_id = response.get_log_id() if hasattr(response, "get_log_id") else ""
    raise RuntimeError(f"{operation} failed, code: {code}, msg: {msg}, log_id: {log_id}")
