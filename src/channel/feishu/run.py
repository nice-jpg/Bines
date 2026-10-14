"""Channel-only Feishu smoke runner."""

from threading import Event

from src.agent_runtime.messaging.messages import IncomingMessage, OutgoingMessage
from src.channel.feishu.channel import FeishuChannel
from src.channel.feishu.config import load_feishu_config
from src.configuration.manager import ConfigurationManager


def main() -> None:
    ConfigurationManager.initialize()
    channel: FeishuChannel = FeishuChannel(load_feishu_config(), "feishu")

    def on_message(message: IncomingMessage) -> bool:
        channel.send_message(
            OutgoingMessage(
                message.channel_id,
                message.conversation_id,
                f"Received text message: {message.text}",
            )
        )
        return True

    channel.bind_message_listener(on_message)
    channel.start()
    try:
        Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()


if __name__ == "__main__":
    main()
