"""Contract tests for websocket publisher normalization and routing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from airco.events import LIVE_EVENT_VERSION, StreamNames, build_live_event_envelope
import ws_publisher.main as ws_main
from ws_publisher.main import build_handler
from ws_publisher.publisher import CentrifugoPublisher


def test_build_live_event_envelope_strips_binary_payload_and_normalizes_metadata():
    envelope = build_live_event_envelope(
        event_type="track_started",
        tenant_id="tenant-7",
        session_id="session-9",
        occurred_at="2026-03-31T10:30:00Z",
        payload={
            "event_type": "track_started",
            "session_id": "session-9",
            "tenant_id": "tenant-7",
            "occurred_at": "2026-03-31T10:30:00Z",
            "camera_id": "camera-1",
            "track_id": 42,
            "crop_b64": "YWJj",
            "full_frame_b64": "ZGVm",
            "embedding": [0.1, 0.2, 0.3],
        },
    )

    assert envelope == {
        "type": "track_started",
        "version": LIVE_EVENT_VERSION,
        "tenant_id": "tenant-7",
        "session_id": "session-9",
        "occurred_at": "2026-03-31T10:30:00Z",
        "payload": {
            "camera_id": "camera-1",
            "track_id": 42,
        },
    }


def test_get_channel_routes_alert_session_and_overview_events():
    pub = CentrifugoPublisher()

    assert (
        pub.get_channel(
            "airco:alerts",
            {
                "event_type": "alert_created",
                "tenant_id": "tenant-7",
                "session_id": "session-9",
            },
        )
        == "alerts:session-9"
    )
    assert (
        pub.get_channel(
            "airco:tracks",
            {
                "event_type": "track_started",
                "tenant_id": "tenant-7",
                "session_id": "session-9",
            },
        )
        == "sessions:session-9"
    )
    assert (
        pub.get_channel(
            "airco:overview",
            {
                "event_type": "overview",
                "tenant_id": "tenant-7",
                "session_id": "session-9",
            },
        )
        == "tenant:tenant-7:overview"
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok"):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, *, response: _FakeResponse | None = None, error: Exception | None = None):
        self.response = response or _FakeResponse()
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client, expected_error",
    [
        (_FakeAsyncClient(response=_FakeResponse(status_code=503, text="boom")), RuntimeError),
        (_FakeAsyncClient(error=httpx.ConnectError("network down")), httpx.ConnectError),
    ],
)
async def test_publish_failure_propagates(monkeypatch, client, expected_error):
    pub = CentrifugoPublisher()
    monkeypatch.setattr("ws_publisher.publisher.httpx.AsyncClient", lambda timeout: client)

    with pytest.raises(expected_error):
        await pub.publish("alerts:session-9", {"type": "alert_created"})


@pytest.mark.asyncio
async def test_publish_normalized_uses_frontend_envelope_and_channel_routing(monkeypatch):
    pub = CentrifugoPublisher()
    captured = {}

    async def fake_publish(channel: str, data: dict):
        captured["channel"] = channel
        captured["data"] = data

    monkeypatch.setattr(pub, "publish", fake_publish)

    await pub.publish_normalized(
        StreamNames.ALERTS,
        {
            "event_type": "alert_created",
            "tenant_id": "tenant-7",
            "session_id": "session-9",
            "timestamp": "2026-03-31T10:30:00Z",
            "camera_id": "camera-1",
            "crop_b64": "YWJj",
            "full_frame_b64": "ZGVm",
            "embedding": [0.1, 0.2, 0.3],
        },
    )

    assert captured["channel"] == "alerts:session-9"
    assert captured["data"] == {
        "type": "alert_created",
        "version": LIVE_EVENT_VERSION,
        "tenant_id": "tenant-7",
        "session_id": "session-9",
        "occurred_at": "2026-03-31T10:30:00Z",
        "payload": {
            "camera_id": "camera-1",
        },
    }


@pytest.mark.asyncio
async def test_overview_stream_routes_to_tenant_overview_even_without_overview_event_type(monkeypatch):
    pub = CentrifugoPublisher()
    captured = {}

    async def fake_publish(channel: str, data: dict):
        captured["channel"] = channel
        captured["data"] = data

    monkeypatch.setattr(pub, "publish", fake_publish)

    await pub.publish_normalized(
        StreamNames.OVERVIEW,
        {
            "event_type": "session_summary",
            "tenant_id": "tenant-7",
            "session_id": "session-9",
            "timestamp": "2026-03-31T10:30:00Z",
            "summary_value": 5,
        },
    )

    assert captured["channel"] == "tenant:tenant-7:overview"
    assert captured["data"]["type"] == "session_summary"


@pytest.mark.asyncio
async def test_build_handler_delegates_to_normalized_publisher():
    pub = AsyncMock()
    handler = build_handler(pub)

    await handler(
        "airco:alerts",
        "1-0",
        {
            "event_type": "alert_created",
            "tenant_id": "tenant-7",
            "session_id": "session-9",
            "timestamp": "2026-03-31T10:30:00Z",
        },
    )

    pub.publish_normalized.assert_awaited_once_with(
        "airco:alerts",
        {
            "event_type": "alert_created",
            "tenant_id": "tenant-7",
            "session_id": "session-9",
            "timestamp": "2026-03-31T10:30:00Z",
        },
    )


@pytest.mark.asyncio
async def test_main_consumes_overview_stream(monkeypatch):
    consumed = {}

    async def fake_consume_multiple_streams(**kwargs):
        consumed.update(kwargs)

    class _FakePublisher:
        def __init__(self):
            self.publish_normalized = AsyncMock()

    monkeypatch.setattr(ws_main, "consume_multiple_streams", fake_consume_multiple_streams)
    monkeypatch.setattr(ws_main, "CentrifugoPublisher", _FakePublisher)

    await ws_main.main()

    assert consumed["streams"] == [
        StreamNames.IDENTITY,
        StreamNames.ALERTS,
        StreamNames.TRACKS,
        StreamNames.OVERVIEW,
    ]
