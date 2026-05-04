"""Redis Streams publish/consume helpers for event-driven architecture."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

import redis.asyncio as aioredis

from airco.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def publish_event(stream: str, event: dict[str, Any], maxlen: int = 10000) -> str:
    """Publish an event to a Redis Stream. Returns the message ID."""
    r = await get_redis()
    # Serialize nested objects to JSON strings for Redis
    flat = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in event.items()}
    msg_id = await r.xadd(stream, flat, maxlen=maxlen, approximate=True)
    return msg_id


async def ensure_consumer_group(stream: str, group: str) -> None:
    """Create consumer group if it doesn't exist. Create stream if needed."""
    r = await get_redis()
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def consume_stream(
    stream: str,
    group: str,
    consumer: str,
    handler: Callable[[str, dict[str, str]], Awaitable[None]],
    batch_size: int = 10,
    block_ms: int = 2000,
) -> None:
    """Consume events from a Redis Stream using consumer groups.

    Runs forever. Calls handler(message_id, fields) for each event.
    Acknowledges after successful processing.
    """
    r = await get_redis()
    await ensure_consumer_group(stream, group)

    while True:
        try:
            messages = await r.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=batch_size,
                block=block_ms,
            )
            if not messages:
                continue

            for stream_name, entries in messages:
                for msg_id, fields in entries:
                    try:
                        # Deserialize JSON strings back to objects
                        parsed = {}
                        for k, v in fields.items():
                            try:
                                parsed[k] = json.loads(v)
                            except (json.JSONDecodeError, TypeError):
                                parsed[k] = v
                        await handler(msg_id, parsed)
                        await r.xack(stream_name, group, msg_id)
                    except Exception:
                        logger.exception(f"Error processing {msg_id} from {stream_name}")

        except aioredis.ConnectionError:
            logger.warning("Redis connection lost, reconnecting...")
            import asyncio
            await asyncio.sleep(1)


async def consume_multiple_streams(
    streams: list[str],
    group: str,
    consumer: str,
    handler: Callable[[str, str, dict[str, str]], Awaitable[None]],
    batch_size: int = 10,
    block_ms: int = 2000,
) -> None:
    """Consume from multiple streams. handler receives (stream_name, msg_id, fields)."""
    r = await get_redis()
    for stream in streams:
        await ensure_consumer_group(stream, group)

    stream_ids = {s: ">" for s in streams}

    while True:
        try:
            messages = await r.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams=stream_ids,
                count=batch_size,
                block=block_ms,
            )
            if not messages:
                continue

            for stream_name, entries in messages:
                for msg_id, fields in entries:
                    try:
                        parsed = {}
                        for k, v in fields.items():
                            try:
                                parsed[k] = json.loads(v)
                            except (json.JSONDecodeError, TypeError):
                                parsed[k] = v
                        await handler(stream_name, msg_id, parsed)
                        await r.xack(stream_name, group, msg_id)
                    except Exception:
                        logger.exception(f"Error processing {msg_id} from {stream_name}")

        except aioredis.ConnectionError:
            logger.warning("Redis connection lost, reconnecting...")
            import asyncio
            await asyncio.sleep(1)
