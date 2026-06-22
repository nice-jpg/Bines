"""Channel-only Feishu smoke runner."""

from __future__ import annotations

try:
    from src.channel.feishu.config import load_feishu_config
    from src.channel.feishu.receiver import FeishuChannelRuntime
except ModuleNotFoundError:  # Supports running with src on PYTHONPATH.
    from channel.feishu.config import load_feishu_config
    from channel.feishu.receiver import FeishuChannelRuntime


def main() -> None:
    channel = FeishuChannelRuntime(load_feishu_config())

    def on_message(message) -> None:
        channel.send_text(message.target, f"Received text message: {message.text}")

    channel.on_parse_error = lambda parsed, _data: (
        channel.send_text(parsed.incoming.target, parsed.error_text or "Failed to parse message.")
        if parsed.incoming is not None
        else None
    )
    channel.start(on_message)


if __name__ == "__main__":
    main()
