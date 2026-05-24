"""Tests for the local session-control bridge."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "services"
        / "session-control"
    ),
)

from session_control.main import (
    ACTIVE_STREAM,
    _bootstrap_rtsp,
    _first_camera_rtsp,
    _handle_control_event,
    _restore_active_session_context,
    _resolve_rtsp_url,
    _session_camera_stream_name,
    _set_active_stream,
)


def test_first_camera_rtsp_reads_first_session_camera():
    fields = {
        "cameras": [
            {"name": "Camera 2", "rtsp_url": "rtsp://example/cam2"},
            {"name": "Camera 3", "rtsp_url": "rtsp://example/cam3"},
        ]
    }

    assert _first_camera_rtsp(fields) == "rtsp://example/cam2"


def test_session_camera_stream_name_is_deterministic():
    assert (
        _session_camera_stream_name("session-123", "camera-456")
        == "session_session_123_camera_456"
    )


def test_bootstrap_rtsp_skips_active_session_alias():
    streams = {
        ACTIVE_STREAM: {"producers": [{"url": "rtsp://example/active"}]},
        "camera_1": {"producers": [{"url": "rtsp://example/cam1"}]},
    }

    assert _bootstrap_rtsp(streams) == "rtsp://example/cam1"


def test_resolve_rtsp_url_rewrites_hostname_to_ip(monkeypatch):
    monkeypatch.setattr("session_control.main.socket.gethostbyname", lambda host: "10.0.0.5")

    # Local hostnames (e.g. .local or no dot) SHOULD be resolved
    resolved_local_suffix = _resolve_rtsp_url("rtsp://user:pass@camera.local:8554/Streaming/Channels/101")
    assert resolved_local_suffix == "rtsp://user:pass@10.0.0.5:8554/Streaming/Channels/101"

    resolved_no_dot = _resolve_rtsp_url("rtsp://user:pass@mycamera:8554/Streaming/Channels/101")
    assert resolved_no_dot == "rtsp://user:pass@10.0.0.5:8554/Streaming/Channels/101"

    # Public hostnames/DDNS domains SHOULD NOT be resolved
    resolved_public = _resolve_rtsp_url("rtsp://user:pass@camera.example.com:8554/Streaming/Channels/101")
    assert resolved_public == "rtsp://user:pass@camera.example.com:8554/Streaming/Channels/101"


@pytest.mark.asyncio
async def test_set_active_stream_accepts_go2rtc_false_negative_400():
    class FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code

        def raise_for_status(self) -> None:
            raise RuntimeError("unexpected 400")

    class FakeClient:
        def __init__(self) -> None:
            self.put_calls = []
            self.get_calls = 0

        async def put(self, url, params):
            self.put_calls.append((url, params))
            return FakeResponse(400)

        async def get(self, url):
            self.get_calls += 1

            class GetResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self):
                    return {
                        ACTIVE_STREAM: {
                            "producers": [
                                {"url": "rtsp://example/cam1"},
                            ]
                        }
                    }

            return GetResponse()

    client = FakeClient()

    await _set_active_stream(client, "rtsp://example/cam1")

    assert client.get_calls == 1


@pytest.mark.asyncio
async def test_restore_active_session_context_uses_latest_unstopped_session(monkeypatch):
    session_id = "session-123"
    camera_id = "camera-456"
    written = {}

    class FakeRedis:
        async def xrevrange(self, stream, count=20):
            return [
                (
                    "2-0",
                    {
                        "event_type": "session_start",
                        "session_id": session_id,
                        "cameras": json.dumps([{"camera_id": camera_id}]),
                    },
                ),
                ("1-0", {"event_type": "session_start", "session_id": "older-session", "cameras": json.dumps([{"camera_id": "older-camera"}])}),
            ]

        async def set(self, key, value):
            written[key] = value

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr("session_control.main.get_redis", fake_get_redis)

    await _restore_active_session_context(object())

    assert written["airco:local:active_session_id"] == session_id
    assert written["airco:local:active_camera_id"] == camera_id


@pytest.mark.asyncio
async def test_restore_active_session_context_persists_all_camera_ids(monkeypatch):
    session_id = "session-123"
    camera_ids = ["camera-1", "camera-2", "camera-3"]
    written = {}

    class FakeRedis:
        async def xrevrange(self, stream, count=20):
            return [
                (
                    "2-0",
                    {
                        "event_type": "session_start",
                        "session_id": session_id,
                        "cameras": json.dumps([{"camera_id": camera_id} for camera_id in camera_ids]),
                    },
                ),
            ]

        async def set(self, key, value):
            written[key] = value

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr("session_control.main.get_redis", fake_get_redis)

    await _restore_active_session_context(object())

    assert written["airco:local:active_session_id"] == session_id
    assert json.loads(written["airco:local:active_camera_ids"]) == camera_ids


@pytest.mark.asyncio
async def test_handle_control_event_updates_all_session_camera_streams(monkeypatch):
    session_id = "session-123"
    cameras = [
        {"camera_id": "camera-1", "rtsp_url": "rtsp://example/cam1", "name": "Camera 1"},
        {"camera_id": "camera-2", "rtsp_url": "rtsp://example/cam2", "name": "Camera 2"},
    ]
    written = {}
    stream_updates = []

    class FakeRedis:
        async def set(self, key, value):
            written[key] = value

    async def fake_get_redis():
        return FakeRedis()

    async def fake_set_active_stream(client, rtsp_url):
        stream_updates.append((ACTIVE_STREAM, rtsp_url))

    async def fake_set_session_camera_stream(client, session_id, camera_id, rtsp_url):
        stream_updates.append((_session_camera_stream_name(session_id, camera_id), rtsp_url))

    monkeypatch.setattr("session_control.main.get_redis", fake_get_redis)
    monkeypatch.setattr("session_control.main._set_active_stream", fake_set_active_stream)
    monkeypatch.setattr("session_control.main._set_session_camera_stream", fake_set_session_camera_stream)

    await _handle_control_event(
        object(),
        {
            "event_type": "session_start",
            "session_id": session_id,
            "cameras": cameras,
        },
    )

    assert written["airco:local:active_session_id"] == session_id
    assert json.loads(written["airco:local:active_camera_ids"]) == ["camera-1", "camera-2"]
    assert stream_updates == [
        (ACTIVE_STREAM, "rtsp://example/cam1"),
        ("session_session_123_camera_1", "rtsp://example/cam1"),
        ("session_session_123_camera_2", "rtsp://example/cam2"),
    ]
