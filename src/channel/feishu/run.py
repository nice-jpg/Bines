"""Channel-only Feishu smoke runner."""

import asyncio

from src.channel.feishu.channel import FeishuChannel
from src.channel.feishu.config import load_feishu_config
from src.channel.gateway import Gateway
from src.channel.messages import OutBoundMessage
from src.configuration.manager import ConfigurationManager


def main() -> None:
    ConfigurationManager.initialize()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


async def serve() -> None:
    channel: FeishuChannel = FeishuChannel(load_feishu_config(), "feishu")
    gateway = Gateway([channel])
    channel.bind_buffer(gateway.buffer)
    await channel.start()
    try:
        while True:
            batch = await gateway.take_batch()
            try:
                for message in batch.messages:
                    await gateway.send(
                        OutBoundMessage(
                            message.channel_id, message.session_id, f"Received text message: {message.text}"
                        )
                    )
                gateway.commit_batch(batch.batch_id)
            except BaseException:
                gateway.release_batch(batch.batch_id)
                raise
    finally:
        gateway.buffer.close()
        await channel.close()


if __name__ == "__main__":
    main()
