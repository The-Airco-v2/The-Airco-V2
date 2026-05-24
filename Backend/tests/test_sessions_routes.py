"""Session route contract tests for Frontend alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from api_fakes import rows_result, scalar_all_result, scalar_one_result


_SESSION_ID = uuid.uuid4()
_CAM_A = uuid.uuid4()
_CAM_B = uuid.uuid4()
_CREATED_AT = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)


def _fake_session(status="stopped", camera_ids=None, started_at=None, stopped_at=None):
    return SimpleNamespace(
        id=_SESSION_ID,
        name="Day Shift",
        status=status,
        config={},
        started_at=started_at,
        stopped_at=stopped_at,
        created_at=_CREATED_AT,
    )


def _fake_session_cameras(camera_ids):
    return [SimpleNamespace(camera_id=cid) for cid in camera_ids]


def test_create_session_returns_201_with_camera_ids(api_client, db_session_mock):
    session = _fake_session()
    db_session_mock.add = lambda _: None
    db_session_mock.flush = AsyncMock()
    # execute call after create: list session_cameras
    db_session_mock.execute.return_value = scalar_all_result(
        _fake_session_cameras([_CAM_A, _CAM_B])
    )
    db_session_mock.refresh.side_effect = lambda s: [
        setattr(s, "id", _SESSION_ID),
        setattr(s, "created_at", _CREATED_AT),
        setattr(s, "status", "stopped"),
        setattr(s, "started_at", None),
        setattr(s, "stopped_at", None),
    ]

    response = api_client.post(
        "/api/v2/sessions",
        json={"name": "Day Shift", "camera_ids": [str(_CAM_A), str(_CAM_B)]},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == str(_SESSION_ID)
    assert data["name"] == "Day Shift"
    assert data["status"] == "stopped"
    assert set(data["camera_ids"]) == {str(_CAM_A), str(_CAM_B)}
    assert data["camera_count"] == 2
    assert "created_at" in data
    assert data["started_at"] is None
    assert data["stopped_at"] is None
    assert data["reid_profile"] == "standard"
    # internal fields must not appear
    assert "profile" not in data
    assert "mode" not in data
    assert "summary" not in data


def test_list_sessions_returns_camera_ids_and_count(api_client, db_session_mock):
    session = _fake_session()
    camera_rows = _fake_session_cameras([_CAM_A])

    # first execute: list sessions; second execute: list session_cameras
    db_session_mock.execute.side_effect = [
        scalar_all_result([session]),
        scalar_all_result(camera_rows),
    ]

    response = api_client.get("/api/v2/sessions")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    s = items[0]
    assert s["camera_ids"] == [str(_CAM_A)]
    assert s["camera_count"] == 1
    assert s["reid_profile"] == "standard"
    assert "profile" not in s
    assert "mode" not in s


def test_list_sessions_returns_ultimate_profile_from_session_config(api_client, db_session_mock):
    session = _fake_session()
    session.config = {"reid_profile": "ultimate"}
    db_session_mock.execute.side_effect = [
        scalar_all_result([session]),
        scalar_all_result(_fake_session_cameras([_CAM_A])),
    ]

    response = api_client.get("/api/v2/sessions")

    assert response.status_code == 200
    assert response.json()[0]["reid_profile"] == "ultimate"


def test_create_session_empty_camera_ids(api_client, db_session_mock):
    db_session_mock.add = lambda _: None
    db_session_mock.flush = AsyncMock()
    db_session_mock.execute.return_value = scalar_all_result([])
    db_session_mock.refresh.side_effect = lambda s: [
        setattr(s, "id", _SESSION_ID),
        setattr(s, "created_at", _CREATED_AT),
        setattr(s, "status", "stopped"),
        setattr(s, "started_at", None),
        setattr(s, "stopped_at", None),
    ]

    response = api_client.post(
        "/api/v2/sessions",
        json={"name": "Empty Session"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["camera_ids"] == []
    assert data["camera_count"] == 0


def test_start_session_returns_running_status(api_client, db_session_mock):
    session = _fake_session()
    publish_event = AsyncMock()

    with patch("airco.redis_streams.publish_event", new=publish_event):
        db_session_mock.execute.side_effect = [
            scalar_one_result(session),
            rows_result([]),
        ]
        response = api_client.post(f"/api/v2/sessions/{_SESSION_ID}/start")

    assert response.status_code == 200
    assert response.json() == {"status": "running", "reid_profile": "standard"}
    publish_event.assert_awaited_once()
    event = publish_event.await_args.args[1]
    assert event["reid_profile"] == "standard"
    assert session.config == {"reid_profile": "standard"}


@pytest.mark.parametrize("requested_profile", ["ultimate", "ultimate_reid"])
def test_start_session_normalizes_ultimate_profile_inputs(
    api_client,
    db_session_mock,
    requested_profile,
):
    session = _fake_session()
    publish_event = AsyncMock()

    with patch("airco.redis_streams.publish_event", new=publish_event):
        db_session_mock.execute.side_effect = [
            scalar_one_result(session),
            rows_result([]),
        ]
        response = api_client.post(
            f"/api/v2/sessions/{_SESSION_ID}/start",
            json={"reid_profile": requested_profile},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "running", "reid_profile": "ultimate"}
    event = publish_event.await_args.args[1]
    assert event["reid_profile"] == "ultimate"
    assert session.config == {"reid_profile": "ultimate"}


def test_stop_session_returns_stopped_status(api_client, db_session_mock):
    with patch("airco.redis_streams.publish_event", new=AsyncMock()):
        response = api_client.post(f"/api/v2/sessions/{_SESSION_ID}/stop")

    assert response.status_code == 200
    assert response.json() == {"status": "stopped"}


def test_get_ultimate_runtime_status_returns_unknown_without_payload(api_client):
    redis = type("Redis", (), {"get": AsyncMock(return_value=None)})()

    with patch("api.routes.sessions.get_redis", new=AsyncMock(return_value=redis)):
        response = api_client.get("/api/v2/sessions/runtime/ultimate-status")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["selector"] == "standard"


def test_get_ultimate_runtime_status_returns_runtime_payload(api_client):
    payload = {
        "status": "ok",
        "selector": "ultimate",
        "active_session_id": str(_SESSION_ID),
        "active_camera_count": 1,
        "worker_count": 1,
        "last_heartbeat_at": _CREATED_AT.isoformat(),
        "workers": [
            {
                "session_id": str(_SESSION_ID),
                "camera_id": str(_CAM_A),
                "rtsp_url": "rtsp://go2rtc/session_1",
                "frames_processed": 42,
                "last_frame_at": _CREATED_AT.isoformat(),
                "last_error": None,
                "running": True,
            }
        ],
    }
    redis = type("Redis", (), {"get": AsyncMock(return_value=__import__("json").dumps(payload))})()

    with patch("api.routes.sessions.get_redis", new=AsyncMock(return_value=redis)):
        response = api_client.get("/api/v2/sessions/runtime/ultimate-status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["selector"] == "ultimate"
    assert body["worker_count"] == 1


def test_get_gpu_status_local_no_gpu(api_client):
    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {}, clear=True):
        response = api_client.get("/api/v2/sessions/runtime/gpu-status")
        
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "local"
    assert body["is_enabled"] is False
    assert body["status"] == "OFF"
    assert body["gpu_name"] == "No local GPU detected"


def test_get_gpu_status_local_env_vars(api_client):
    env = {
        "LOCAL_GPU_NAME": "NVIDIA GeForce RTX 3060",
        "LOCAL_GPU_MEM": "6144",
        "LOCAL_GPU_UUID": "GPU-xyz"
    }
    with patch.dict("os.environ", env, clear=True):
        response = api_client.get("/api/v2/sessions/runtime/gpu-status")
        
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "local"
    assert body["is_enabled"] is True
    assert body["status"] == "ON"
    assert body["gpu_name"] == "NVIDIA GeForce RTX 3060"
    assert body["memory"] == "6144 MiB VRAM"
    assert body["gpu_id"] == "GPU-xyz"
