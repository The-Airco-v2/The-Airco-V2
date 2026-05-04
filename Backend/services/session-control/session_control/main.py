"""Session control bridge.

Keeps a stable go2rtc relay stream named ``active_session`` pointed at the
camera selected by the most recent session control event. Local-lite Savant
consumes that single relay path instead of a hardcoded camera stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.redis_streams import consume_stream, get_redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("session-control")

GROUP = "session-control-group"
CONSUMER = f"session-control-{os.getpid()}"
CONTROL_STREAM = "airco:control"
ACTIVE_STREAM = "active_session"
READY_FILE = Path("/tmp/session-control-ready")
GO2RTC_URL = os.getenv("GO2RTC_URL", "http://host.docker.internal:1984")
ACTIVE_CAMERA_IDS_KEY = "airco:local:active_camera_ids"


def _parse_cameras(fields: dict[str, Any]) -> list[dict[str, Any]]:
    cameras = fields.get("cameras")
    if isinstance(cameras, str):
        try:
            cameras = json.loads(cameras)
        except json.JSONDecodeError:
            return []
    return cameras if isinstance(cameras, list) else []


def _sanitize_stream_part(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")


def _session_camera_stream_name(session_id: Any, camera_id: Any) -> str:
    return f"session_{_sanitize_stream_part(session_id)}_{_sanitize_stream_part(camera_id)}"


def _camera_ids(cameras: list[dict[str, Any]]) -> list[str]:
    return [
        str(camera["camera_id"])
        for camera in cameras
        if isinstance(camera, dict) and camera.get("camera_id")
    ]


def _first_camera_rtsp(fields: dict[str, Any]) -> str | None:
    cameras = _parse_cameras(fields)
    if not cameras:
        return None
    first = cameras[0]
    if not isinstance(first, dict):
        return None
    rtsp_url = first.get("rtsp_url")
    return rtsp_url if isinstance(rtsp_url, str) and rtsp_url else None


def _resolve_rtsp_url(rtsp_url: str) -> str:
    try:
        parsed = urlparse(rtsp_url)
        hostname = parsed.hostname
        if hostname and not hostname.replace(".", "").isdigit():
            ip = socket.gethostbyname(hostname)
            netloc = parsed.netloc.replace(hostname, ip, 1)
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        logger.exception("failed to resolve RTSP hostname")
    return rtsp_url


def _bootstrap_rtsp(streams: dict[str, Any]) -> str | None:
    for stream_name, config in streams.items():
        if stream_name == ACTIVE_STREAM or not isinstance(config, dict):
            continue
        producers = config.get("producers")
        if not isinstance(producers, list):
            continue
        for producer in producers:
            if not isinstance(producer, dict):
                continue
            url = producer.get("url")
            if isinstance(url, str) and url:
                return url
    return None


async def _list_streams(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(f"{GO2RTC_URL}/api/streams")
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _stream_matches_rtsp(streams: dict[str, Any], rtsp_url: str) -> bool:
    active = streams.get(ACTIVE_STREAM)
    if not isinstance(active, dict):
        return False
    producers = active.get("producers")
    if not isinstance(producers, list):
        return False
    for producer in producers:
        if not isinstance(producer, dict):
            continue
        if producer.get("url") == rtsp_url:
            return True
    return False


async def _set_active_stream(client: httpx.AsyncClient, rtsp_url: str) -> None:
    resolved_rtsp_url = _resolve_rtsp_url(rtsp_url)
    response = await client.put(
        f"{GO2RTC_URL}/api/streams",
        params={"name": ACTIVE_STREAM, "src": resolved_rtsp_url},
    )
    if response.status_code >= 400:
        streams = await _list_streams(client)
        if _stream_matches_rtsp(streams, resolved_rtsp_url):
            logger.info("go2rtc active_session updated despite %d response", response.status_code)
            return
        response.raise_for_status()
    logger.info("go2rtc active_session updated")


async def _set_session_camera_stream(
    client: httpx.AsyncClient,
    session_id: Any,
    camera_id: Any,
    rtsp_url: str,
) -> None:
    resolved_rtsp_url = _resolve_rtsp_url(rtsp_url)
    stream_name = _session_camera_stream_name(session_id, camera_id)
    response = await client.put(
        f"{GO2RTC_URL}/api/streams",
        params={"name": stream_name, "src": resolved_rtsp_url},
    )
    if response.status_code >= 400:
        streams = await _list_streams(client)
        stream = streams.get(stream_name)
        producers = stream.get("producers") if isinstance(stream, dict) else None
        if isinstance(producers, list) and any(
            isinstance(producer, dict) and producer.get("url") == resolved_rtsp_url
            for producer in producers
        ):
            logger.info("go2rtc %s updated despite %d response", stream_name, response.status_code)
            return
        response.raise_for_status()
    logger.info("go2rtc %s updated", stream_name)


async def _bootstrap_active_stream(client: httpx.AsyncClient) -> bool:
    streams = await _list_streams(client)
    rtsp_url = _bootstrap_rtsp(streams)
    if not rtsp_url:
        return False
    await _set_active_stream(client, rtsp_url)
    return True


async def _handle_control_event(client: httpx.AsyncClient, fields: dict[str, Any]) -> None:
    event_type = fields.get("event_type", "")
    redis = await get_redis()
    if event_type == "session_start":
        session_id = fields.get("session_id")
        cameras = _parse_cameras(fields)
        if session_id:
            await redis.set("airco:local:active_session_id", str(session_id))
        if cameras:
            camera_ids = _camera_ids(cameras)
            await redis.set(ACTIVE_CAMERA_IDS_KEY, json.dumps(camera_ids))
            first_camera = cameras[0]
            if isinstance(first_camera, dict) and first_camera.get("camera_id"):
                await redis.set("airco:local:active_camera_id", str(first_camera["camera_id"]))
            rtsp_url = _first_camera_rtsp(fields)
            if rtsp_url:
                await _set_active_stream(client, rtsp_url)
            for camera in cameras:
                if not isinstance(camera, dict):
                    continue
                camera_id = camera.get("camera_id")
                rtsp_url = camera.get("rtsp_url")
                if camera_id and isinstance(rtsp_url, str) and rtsp_url:
                    await _set_session_camera_stream(client, session_id, camera_id, rtsp_url)
            return
        if rtsp_url := _first_camera_rtsp(fields):
            await _set_active_stream(client, rtsp_url)
            return
        logger.warning("session_start missing camera RTSP payload")
        return

    if event_type in {"session_stop", "session_pause"}:
        await redis.delete(
            "airco:local:active_session_id",
            "airco:local:active_camera_id",
            ACTIVE_CAMERA_IDS_KEY,
        )
        bootstrapped = await _bootstrap_active_stream(client)
        if not bootstrapped:
            logger.info("no fallback go2rtc stream available after %s", event_type)


async def _wait_until_bootstrapped(client: httpx.AsyncClient) -> None:
    while True:
        try:
            if await _bootstrap_active_stream(client):
                READY_FILE.write_text("ready", encoding="utf-8")
                return
        except Exception:
            logger.exception("failed to bootstrap active_session")
        await asyncio.sleep(2)


async def _restore_active_session_context(client: httpx.AsyncClient) -> None:
    redis = await get_redis()
    entries = await redis.xrevrange(CONTROL_STREAM, count=20)
    stopped_sessions: set[str] = set()

    for _, fields in entries:
        event_type = fields.get("event_type", "")
        session_id = fields.get("session_id")
        cameras = _parse_cameras(fields)

        if event_type in {"session_stop", "session_pause"} and session_id:
            stopped_sessions.add(str(session_id))
            continue

        if event_type != "session_start" or not session_id or str(session_id) in stopped_sessions:
            continue

        await redis.set("airco:local:active_session_id", str(session_id))
        if cameras:
            await redis.set(ACTIVE_CAMERA_IDS_KEY, json.dumps(_camera_ids(cameras)))
            first_camera = cameras[0]
            if isinstance(first_camera, dict) and first_camera.get("camera_id"):
                await redis.set("airco:local:active_camera_id", str(first_camera["camera_id"]))
            for camera in cameras:
                if not isinstance(camera, dict):
                    continue
                camera_id = camera.get("camera_id")
                rtsp_url = camera.get("rtsp_url")
                if camera_id and isinstance(rtsp_url, str) and rtsp_url:
                    try:
                        await _set_session_camera_stream(client, session_id, camera_id, rtsp_url)
                    except Exception:
                        logger.exception("failed to register go2rtc stream for camera=%s", camera_id)
        logger.info("Restored active session context: session=%s cameras=%d", session_id, len(cameras))
        return


async def main() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        await _wait_until_bootstrapped(client)
        await _restore_active_session_context(client)

        async def handler(msg_id: str, fields: dict[str, Any]) -> None:
            await _handle_control_event(client, fields)

        logger.info("Session control bridge starting...")
        await consume_stream(
            stream=CONTROL_STREAM,
            group=GROUP,
            consumer=CONSUMER,
            handler=handler,
        )


if __name__ == "__main__":
    asyncio.run(main())
