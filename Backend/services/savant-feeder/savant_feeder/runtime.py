"""Pure runtime helpers for the Savant multi-camera feeder."""

from __future__ import annotations

import json
import re


def sanitize_stream_part(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")


def session_camera_stream_name(session_id: object, camera_id: object) -> str:
    return f"session_{sanitize_stream_part(session_id)}_{sanitize_stream_part(camera_id)}"


def stream_rtsp_url(base_url: str, session_id: str, camera_id: str) -> str:
    stream_name = session_camera_stream_name(session_id, camera_id)
    return f"{base_url.rstrip('/')}/{stream_name}"


def parse_active_camera_ids(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if item]
