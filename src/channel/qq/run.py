"""Channel-only QQ smoke runner."""

import asyncio

from src.channel.gateway import Gateway
from src.channel.messages import OutBoundMessage
from src.channel.qq.channel import QQChannel
from src.channel.qq.config import load_qq_config
from src.configuration.manager import ConfigurationManager


def main() -> None:
    ConfigurationManager.initialize()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


async def serve() -> None:
    channel: QQChannel = QQChannel(load_qq_config(), "qq")
    gateway = Gateway([channel])
    channel.bind_buffer(gateway.buffer)
    try:
        await channel.start()
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
