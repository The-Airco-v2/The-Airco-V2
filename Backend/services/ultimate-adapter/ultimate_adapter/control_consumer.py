"""Consume control events and persist the adapter-scoped session contract."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.redis_streams import consume_stream, get_redis
from ultimate_adapter.config import (
    ACTIVE_CAMERA_IDS_KEY,
    ACTIVE_SELECTOR_KEY,
    ACTIVE_SESSION_KEY,
    CONTROL_GROUP,
    CONTROL_STREAM,
    SESSION_ALIAS_CONTRACT_KEY,
    UltimateAdapterSettings,
    load_settings,
    selector_from_fields,
    ULTIMATE_SELECTOR,
)
from ultimate_adapter.stream_runtime import session_alias_contract_payload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ultimate-adapter")

READY_FILE = Path("/tmp/ultimate-adapter-ready")


def _parse_cameras(fields: dict[str, Any]) -> list[dict[str, Any]]:
    cameras = fields.get("cameras")
    if isinstance(cameras, str):
        try:
            cameras = json.loads(cameras)
        except json.JSONDecodeError:
            return []
    return cameras if isinstance(cameras, list) else []


def _camera_ids(cameras: list[dict[str, Any]]) -> list[str]:
    return [
        str(camera["camera_id"])
        for camera in cameras
        if isinstance(camera, dict) and camera.get("camera_id")
    ]


def _alias_contracts(settings: UltimateAdapterSettings, session_id: Any, cameras: list[dict[str, Any]]) -> list[dict[str, str]]:
    contracts: list[dict[str, str]] = []
    for camera in cameras:
        camera_id = camera.get("camera_id") if isinstance(camera, dict) else None
        if not camera_id:
            continue
        contracts.append(
            session_alias_contract_payload(
                base_url=settings.go2rtc_rtsp_base_url,
                session_id=session_id,
                camera_id=camera_id,
            )
        )
    return contracts


async def _clear_session_state(redis_client) -> None:
    await redis_client.delete(
        ACTIVE_SESSION_KEY,
        ACTIVE_CAMERA_IDS_KEY,
        ACTIVE_SELECTOR_KEY,
        SESSION_ALIAS_CONTRACT_KEY,
    )


async def _persist_session_state(
    redis_client,
    *,
    settings: UltimateAdapterSettings,
    fields: dict[str, Any],
) -> None:
    session_id = fields.get("session_id")
    if not session_id:
        logger.warning("session_start missing session_id")
        return

    cameras = _parse_cameras(fields)
    camera_ids = _camera_ids(cameras)
    selector = selector_from_fields(fields)
    contracts = _alias_contracts(settings, session_id, cameras)

    await redis_client.set(ACTIVE_SESSION_KEY, str(session_id))
    await redis_client.set(ACTIVE_SELECTOR_KEY, selector)
    await redis_client.set(ACTIVE_CAMERA_IDS_KEY, json.dumps(camera_ids))
    await redis_client.set(SESSION_ALIAS_CONTRACT_KEY, json.dumps(contracts))

    logger.info(
        "recorded ultimate-adapter control state session=%s selector=%s cameras=%d aliases=%d",
        session_id,
        selector,
        len(camera_ids),
        len(contracts),
    )


async def _active_session_state(redis_client) -> tuple[str | None, str | None]:
    active_session_id, active_selector = await redis_client.mget(
        ACTIVE_SESSION_KEY,
        ACTIVE_SELECTOR_KEY,
    )
    return (
        str(active_session_id) if active_session_id else None,
        str(active_selector) if active_selector else None,
    )


async def handle_control_event(
    redis_client,
    *,
    settings: UltimateAdapterSettings,
    fields: dict[str, Any],
) -> None:
    event_type = fields.get("event_type", "")
    if event_type == "session_start":
        selector = selector_from_fields(fields)
        active_session_id, active_selector = await _active_session_state(redis_client)

        if selector != ULTIMATE_SELECTOR:
            if (
                selector == "standard"
                and active_selector == ULTIMATE_SELECTOR
                and active_session_id is not None
            ):
                await _clear_session_state(redis_client)
                logger.info(
                    "released ultimate-adapter control state for standard handoff session_id=%s",
                    active_session_id,
                )
            else:
                logger.info("ignoring non-ultimate session_start selector=%s", selector)
            return
        await _persist_session_state(redis_client, settings=settings, fields=fields)
        return

    if event_type in {"session_stop", "session_pause"}:
        active_session_id, active_selector = await _active_session_state(redis_client)
        session_id = fields.get("session_id")
        if (
            active_selector == ULTIMATE_SELECTOR
            and active_session_id is not None
            and session_id is not None
            and active_session_id == str(session_id)
        ):
            await _clear_session_state(redis_client)
            logger.info("cleared ultimate-adapter control state after %s", event_type)
        else:
            logger.info(
                "ignoring %s for non-owned session session_id=%s active_session_id=%s active_selector=%s",
                event_type,
                session_id,
                active_session_id,
                active_selector,
            )


async def main() -> None:
    settings = load_settings()
    redis_client = await get_redis()

    async def handler(msg_id: str, fields: dict[str, Any]) -> None:
        await handle_control_event(redis_client, settings=settings, fields=fields)

    READY_FILE.write_text("ready", encoding="utf-8")
    logger.info("Ultimate adapter control consumer starting...")
    await consume_stream(
        stream=CONTROL_STREAM,
        group=CONTROL_GROUP,
        consumer=f"ultimate-adapter-{os.getpid()}",
        handler=handler,
    )


if __name__ == "__main__":
    asyncio.run(main())
