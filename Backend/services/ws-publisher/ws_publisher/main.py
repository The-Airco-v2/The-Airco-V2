"""WS Publisher -- bridges Redis Streams events to Centrifugo WebSocket channels."""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.events import StreamNames
from airco.redis_streams import consume_multiple_streams
from ws_publisher.publisher import CentrifugoPublisher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ws-publisher")

GROUP = "ws-group"
CONSUMER = f"ws-{os.getpid()}"


def build_handler(pub: CentrifugoPublisher):
    async def handler(stream: str, msg_id: str, fields: dict):
        await pub.publish_normalized(stream, fields)

    return handler


async def main():
    pub = CentrifugoPublisher()

    logger.info("WS Publisher starting...")
    await consume_multiple_streams(
        streams=[StreamNames.IDENTITY, StreamNames.ALERTS, StreamNames.TRACKS, StreamNames.OVERVIEW],
        group=GROUP,
        consumer=CONSUMER,
        handler=build_handler(pub),
    )


if __name__ == "__main__":
    asyncio.run(main())
