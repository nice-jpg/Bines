"""Application probing task context."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from src.prompts import build_initial_messages
except ModuleNotFoundError:  # Supports running with src on PYTHONPATH.
    from prompts import build_initial_messages

APP_PROBE_TRIGGER = "执行应用探测任务"
FEISHU_TEXT_RE = re.compile(r"<text>(.*?)</text>", re.DOTALL)


def is_app_probe_request(text: str) -> bool:
    """Return true only for the exact application probing trigger."""

    return text == APP_PROBE_TRIGGER


def messages_request_app_probe(messages: Sequence[Any]) -> bool:
    """Detect the app-probe trigger in plain or Feishu-wrapped user messages."""

    for message in messages:
        content = _message_content(message)
        if content is None:
            continue
        if is_app_probe_request(content):
            return True
        if _feishu_text_triggers_app_probe(content):
            return True
    return False


def build_app_probe_messages(
    workspace_dir: str | Path | None = None,
    *,
    shell: str = "zsh",
    current_date: str | None = None,
    timezone: str = "Asia/Shanghai",
) -> list[Mapping[str, str]]:
    """Build task messages for application probing."""

    return [
        {"role": "user", "content": content}
        for content in build_initial_messages(
            workspace_dir,
            shell=shell,
            current_date=current_date,
            timezone=timezone,
        )
    ]


def with_app_probe_messages(messages: Sequence[Any]) -> list[Any]:
    """Prepend app-probe context when a message strictly requests the task."""

    prepared = list(messages)
    if not messages_request_app_probe(prepared):
        return prepared
    return [*build_app_probe_messages(), *prepared]


def _message_content(message: Any) -> str | None:
    if isinstance(message, Mapping):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    return content if isinstance(content, str) else None


def _feishu_text_triggers_app_probe(content: str) -> bool:
    if "<feishu_message>" not in content:
        return False
    match = FEISHU_TEXT_RE.search(content)
    if match is None:
        return False
    return is_app_probe_request(unescape(match.group(1)))
