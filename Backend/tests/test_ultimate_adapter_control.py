"""Focused tests for the ultimate adapter control/runtime scaffold."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"),
)

from ultimate_adapter.config import (
    ACTIVE_CAMERA_IDS_KEY,
    ACTIVE_SELECTOR_KEY,
    ACTIVE_SESSION_KEY,
    SESSION_ALIAS_CONTRACT_KEY,
    normalize_selector,
)
from ultimate_adapter.control_consumer import handle_control_event
from ultimate_adapter.stream_runtime import (
    build_session_alias_contract,
    session_camera_stream_name,
    session_camera_stream_url,
)


def test_normalize_selector_maps_legacy_and_unknown_values():
    assert normalize_selector("standard") == "standard"
    assert normalize_selector("ultimate") == "ultimate"
    assert normalize_selector("ultimate_reid") == "ultimate"
    assert normalize_selector("ULTIMATE") == "ultimate"
    assert normalize_selector("something-else") == "standard"
    assert normalize_selector(None) == "standard"


def test_session_alias_helpers_are_session_scoped_only():
    contract = build_session_alias_contract(
        base_url="rtsp://host.docker.internal:8556",
        session_id="session-123",
        camera_id="camera-456",
    )

    assert contract.stream_name == "session_session_123_camera_456"
    assert contract.rtsp_url == "rtsp://host.docker.internal:8556/session_session_123_camera_456"
    assert session_camera_stream_name("session-123", "camera-456") == contract.stream_name
    assert session_camera_stream_url("rtsp://host.docker.internal:8556/", "session-123", "camera-456") == contract.rtsp_url


def test_handle_control_event_persists_canonical_selector_and_alias_contracts():
    written: dict[str, str] = {}

    class FakeRedis:
        async def set(self, key, value):
            written[key] = value

        async def delete(self, *keys):
            for key in keys:
                written.pop(key, None)

        async def mget(self, *keys):
            return [written.get(key) for key in keys]

    fields = {
        "event_type": "session_start",
        "session_id": "session-123",
        "reid_profile": "ultimate_reid",
        "cameras": json.dumps(
            [
                {
                    "camera_id": "camera-1",
                    "rtsp_url": "rtsp://db.example/camera-1",
                },
                {
                    "camera_id": "camera-2",
                    "rtsp_url": "rtsp://db.example/camera-2",
                },
            ]
        ),
    }

    import asyncio

    asyncio.run(
        handle_control_event(
            FakeRedis(),
            settings=type(
                "Settings",
                (),
                {"go2rtc_rtsp_base_url": "rtsp://host.docker.internal:8556"},
            )(),
            fields=fields,
        )
    )

    assert written[ACTIVE_SESSION_KEY] == "session-123"
    assert written[ACTIVE_SELECTOR_KEY] == "ultimate"
    assert json.loads(written[ACTIVE_CAMERA_IDS_KEY]) == ["camera-1", "camera-2"]
    assert json.loads(written[SESSION_ALIAS_CONTRACT_KEY]) == [
        {
            "session_id": "session-123",
            "camera_id": "camera-1",
            "stream_name": "session_session_123_camera_1",
            "rtsp_url": "rtsp://host.docker.internal:8556/session_session_123_camera_1",
        },
        {
            "session_id": "session-123",
            "camera_id": "camera-2",
            "stream_name": "session_session_123_camera_2",
            "rtsp_url": "rtsp://host.docker.internal:8556/session_session_123_camera_2",
        },
    ]


def test_handle_control_event_ignores_standard_sessions():
    written: dict[str, str] = {}

    class FakeRedis:
        async def set(self, key, value):
            written[key] = value

        async def delete(self, *keys):
            for key in keys:
                written.pop(key, None)

        async def mget(self, *keys):
            return [written.get(key) for key in keys]

    fields = {
        "event_type": "session_start",
        "session_id": "session-123",
        "reid_profile": "standard",
        "cameras": json.dumps(
            [
                {
                    "camera_id": "camera-1",
                    "rtsp_url": "rtsp://db.example/camera-1",
                }
            ]
        ),
    }

    import asyncio

    asyncio.run(
        handle_control_event(
            FakeRedis(),
            settings=type(
                "Settings",
                (),
                {"go2rtc_rtsp_base_url": "rtsp://host.docker.internal:8556"},
            )(),
            fields=fields,
        )
    )

    assert written == {}


def test_handle_control_event_releases_ultimate_state_on_standard_handoff():
    written = {
        ACTIVE_SESSION_KEY: "session-123",
        ACTIVE_CAMERA_IDS_KEY: '["camera-1"]',
        ACTIVE_SELECTOR_KEY: "ultimate",
        SESSION_ALIAS_CONTRACT_KEY: '[{"session_id":"session-123"}]',
    }

    class FakeRedis:
        async def set(self, key, value):
            written[key] = value

        async def delete(self, *keys):
            for key in keys:
                written.pop(key, None)

        async def mget(self, *keys):
            return [written.get(key) for key in keys]

    import asyncio

    asyncio.run(
        handle_control_event(
            FakeRedis(),
            settings=type(
                "Settings",
                (),
                {"go2rtc_rtsp_base_url": "rtsp://host.docker.internal:8556"},
            )(),
            fields={
                "event_type": "session_start",
                "session_id": "session-456",
                "reid_profile": "standard",
                "cameras": json.dumps(
                    [
                        {
                            "camera_id": "camera-2",
                            "rtsp_url": "rtsp://db.example/camera-2",
                        }
                    ]
                ),
            },
        )
    )

    assert written == {}


def test_handle_control_event_clears_state_on_stop():
    written = {
        ACTIVE_SESSION_KEY: "session-123",
        ACTIVE_CAMERA_IDS_KEY: "[]",
        ACTIVE_SELECTOR_KEY: "ultimate",
        SESSION_ALIAS_CONTRACT_KEY: "[]",
    }

    class FakeRedis:
        async def set(self, key, value):
            written[key] = value

        async def delete(self, *keys):
            for key in keys:
                written.pop(key, None)

        async def mget(self, *keys):
            return [written.get(key) for key in keys]

    import asyncio

    asyncio.run(
        handle_control_event(
            FakeRedis(),
            settings=type(
                "Settings",
                (),
                {"go2rtc_rtsp_base_url": "rtsp://host.docker.internal:8556"},
            )(),
            fields={"event_type": "session_stop", "session_id": "session-123"},
        )
    )

    assert written == {}


def test_handle_control_event_ignores_stop_for_non_owned_session():
    written = {
        ACTIVE_SESSION_KEY: "session-123",
        ACTIVE_CAMERA_IDS_KEY: "[]",
        ACTIVE_SELECTOR_KEY: "standard",
        SESSION_ALIAS_CONTRACT_KEY: "[]",
    }

    class FakeRedis:
        async def set(self, key, value):
            written[key] = value

        async def delete(self, *keys):
            for key in keys:
                written.pop(key, None)

        async def mget(self, *keys):
            return [written.get(key) for key in keys]

    import asyncio

    asyncio.run(
        handle_control_event(
            FakeRedis(),
            settings=type(
                "Settings",
                (),
                {"go2rtc_rtsp_base_url": "rtsp://host.docker.internal:8556"},
            )(),
            fields={"event_type": "session_stop", "session_id": "session-123"},
        )
    )

    assert written == {
        ACTIVE_SESSION_KEY: "session-123",
        ACTIVE_CAMERA_IDS_KEY: "[]",
        ACTIVE_SELECTOR_KEY: "standard",
        SESSION_ALIAS_CONTRACT_KEY: "[]",
    }
