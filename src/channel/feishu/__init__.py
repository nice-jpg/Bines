"""Feishu communication channel for the Bines agent."""

from .client import FeishuMessenger
from .config import FeishuConfig, load_feishu_config
from .dedup import MessageDeduplicator
from .messages import IncomingMessage, MessageTarget, ParsedMessage, parse_message_event
from .receiver import FeishuChannel, FeishuChannelRuntime

__all__ = [
    "FeishuChannel",
    "FeishuChannelRuntime",
    "FeishuConfig",
    "FeishuMessenger",
    "IncomingMessage",
    "MessageDeduplicator",
    "MessageTarget",
    "ParsedMessage",
    "load_feishu_config",
    "parse_message_event",
]
