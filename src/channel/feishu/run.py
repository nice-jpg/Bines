"""Run the Feishu long-connection channel."""

from __future__ import annotations

try:
    from src.channel.feishu.agent_bridge import FeishuAgentBridge
    from src.channel.feishu.client import FeishuMessenger
    from src.channel.feishu.config import load_feishu_config
    from src.channel.feishu.receiver import FeishuChannel
except ModuleNotFoundError:  # Supports running with src on PYTHONPATH.
    from channel.feishu.agent_bridge import FeishuAgentBridge
    from channel.feishu.client import FeishuMessenger
    from channel.feishu.config import load_feishu_config
    from channel.feishu.receiver import FeishuChannel


def main() -> None:
    config = load_feishu_config()
    messenger = FeishuMessenger(config)
    bridge = FeishuAgentBridge(messenger)

    def on_parse_error(error_text, data) -> None:
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        chat_id = getattr(message, "chat_id", "")
        message_id = getattr(message, "message_id", "")
        chat_type = getattr(message, "chat_type", "")
        if chat_type == "p2p" and chat_id:
            messenger.send_text(chat_id, error_text)
        elif message_id:
            messenger.reply_text(message_id, error_text)

    channel = FeishuChannel(config, bridge.handle_message, on_parse_error=on_parse_error)
    channel.start()


if __name__ == "__main__":
    main()
