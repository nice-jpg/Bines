"""Feishu long-connection message receiver."""

from __future__ import annotations

from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .config import FeishuConfig
from .messages import IncomingMessage, parse_message_event


MessageHandler = Callable[[IncomingMessage], None]


class FeishuChannel:
    """Receive Feishu text messages through the official long connection client."""

    def __init__(
        self,
        config: FeishuConfig,
        on_message: MessageHandler,
        *,
        on_parse_error: Callable[[str, P2ImMessageReceiveV1], None] | None = None,
    ) -> None:
        self.config = config
        self.on_message = on_message
        self.on_parse_error = on_parse_error
        self.event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message)
            .build()
        )
        self.ws_client = lark.ws.Client(
            config.app_id,
            config.app_secret,
            event_handler=self.event_handler,
            log_level=_log_level(config.log_level),
        )

    def start(self) -> None:
        self.ws_client.start()

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        parsed = parse_message_event(data)
        if parsed.incoming is not None:
            self.on_message(parsed.incoming)
            return
        if self.on_parse_error is not None and parsed.error_text:
            self.on_parse_error(parsed.error_text, data)


def _log_level(value: str):
    normalized = str(value or "INFO").upper()
    return getattr(lark.LogLevel, normalized, lark.LogLevel.INFO)
