"""Feishu long-connection channel runtime."""

from __future__ import annotations

from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .client import FeishuMessenger
from .config import FeishuConfig
from .dedup import MessageDeduplicator
from .messages import IncomingMessage, MessageTarget, ParsedMessage, parse_message_event


MessageHandler = Callable[[IncomingMessage], None]
ParseErrorHandler = Callable[[ParsedMessage, P2ImMessageReceiveV1], None]


class FeishuChannelRuntime:
    """Transport-only Feishu runtime. The agent owns message handling."""

    def __init__(
        self,
        config: FeishuConfig,
        messenger: FeishuMessenger | None = None,
        deduplicator: MessageDeduplicator | None = None,
        *,
        on_parse_error: ParseErrorHandler | None = None,
    ) -> None:
        self.config = config
        self.messenger = messenger or FeishuMessenger(config)
        self.deduplicator = deduplicator or MessageDeduplicator()
        self.on_parse_error = on_parse_error
        self._on_message: MessageHandler | None = None
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

    def start(self, on_message: MessageHandler) -> None:
        self._on_message = on_message
        self.ws_client.start()

    def send_text(self, target: MessageTarget, text: str) -> None:
        if target.chat_type == "p2p":
            self.messenger.send_text(target.chat_id, text)
        else:
            self.messenger.reply_text(target.message_id, text)

    def build_notifier(self, target: MessageTarget) -> Callable[[str], None]:
        return lambda text: self.messenger.send_text(target.chat_id, text)

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        parsed = parse_message_event(data)
        incoming = parsed.incoming
        if incoming is None:
            self._handle_parse_error(parsed, data)
            return
        if not self.deduplicator.should_process(incoming.message_id, chat_id=incoming.chat_id):
            return
        if parsed.ok:
            if self._on_message is not None:
                self._on_message(incoming)
            return
        self._handle_parse_error(parsed, data)

    def _handle_parse_error(self, parsed: ParsedMessage, data: P2ImMessageReceiveV1) -> None:
        if self.on_parse_error is not None:
            self.on_parse_error(parsed, data)


FeishuChannel = FeishuChannelRuntime


def _log_level(value: str):
    normalized = str(value or "INFO").upper()
    return getattr(lark.LogLevel, normalized, lark.LogLevel.INFO)
