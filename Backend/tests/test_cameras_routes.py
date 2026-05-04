"""Camera route contract tests for Frontend alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from api_fakes import scalar_all_result, scalar_one_result


def test_create_camera_returns_201_with_canonical_shape(api_client, db_session_mock):
    camera_id = uuid.uuid4()
    created_at = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)

    db_session_mock.add = lambda _: None
    db_session_mock.refresh.side_effect = lambda cam: [
        setattr(cam, "id", camera_id),
        setattr(cam, "created_at", created_at),
        setattr(cam, "is_active", True),
        setattr(cam, "location", "Lobby"),
        setattr(cam, "zone", "Front"),
    ]

    response = api_client.post(
        "/api/v2/cameras",
        json={
            "name": "Entrance Cam",
            "rtsp_url": "rtsp://192.168.1.10/live",
            "location": "Lobby",
            "zone": "Front",
            "is_entrance": True,
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == str(camera_id)
    assert data["name"] == "Entrance Cam"
    assert data["rtsp_url"] == "rtsp://192.168.1.10/live"
    assert data["location"] == "Lobby"
    assert data["zone"] == "Front"
    assert data["is_entrance"] is True
    assert data["status"] in ("online", "offline", "unknown")
    assert "last_seen" in data
    assert "created_at" in data
    assert "is_active" not in data
    assert data["stream_name"] == "entrance_cam"


def test_create_camera_active_maps_to_online_status(api_client, db_session_mock):
    camera_id = uuid.uuid4()
    created_at = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)

    db_session_mock.add = lambda _: None
    db_session_mock.refresh.side_effect = lambda cam: [
        setattr(cam, "id", camera_id),
        setattr(cam, "created_at", created_at),
        setattr(cam, "is_active", True),
        setattr(cam, "location", None),
        setattr(cam, "zone", None),
    ]

    response = api_client.post(
        "/api/v2/cameras",
        json={"name": "Cam", "rtsp_url": "rtsp://x/live"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "online"


def test_create_camera_inactive_maps_to_offline_status(api_client, db_session_mock):
    camera_id = uuid.uuid4()
    created_at = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)

    db_session_mock.add = lambda _: None
    db_session_mock.refresh.side_effect = lambda cam: [
        setattr(cam, "id", camera_id),
        setattr(cam, "created_at", created_at),
        setattr(cam, "is_active", False),
        setattr(cam, "location", None),
        setattr(cam, "zone", None),
    ]

    response = api_client.post(
        "/api/v2/cameras",
        json={"name": "Cam", "rtsp_url": "rtsp://x/live"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "offline"


def test_list_cameras_returns_canonical_shape(api_client, db_session_mock):
    created_at = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    db_session_mock.execute.return_value = scalar_all_result(
        [
            SimpleNamespace(
                id=uuid.uuid4(),
                name="Cam A",
                rtsp_url="rtsp://a/live",
                location="Lobby",
                zone="Front",
                is_entrance=True,
                is_active=True,
                created_at=created_at,
            )
        ]
    )

    response = api_client.get("/api/v2/cameras")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    cam = items[0]
    assert cam["name"] == "Cam A"
    assert cam["status"] == "online"
    assert "is_active" not in cam
    assert "created_at" in cam
    assert "last_seen" in cam
    assert cam["stream_name"] == "cam_a"


def test_delete_camera_returns_204(api_client, db_session_mock):
    from unittest.mock import AsyncMock as _AsyncMock

    camera_id = uuid.uuid4()
    db_session_mock.execute.return_value = scalar_one_result(
        SimpleNamespace(
            id=camera_id,
            name="Cam",
            tenant_id="default",
        )
    )
    db_session_mock.delete = _AsyncMock(return_value=None)

    response = api_client.delete(f"/api/v2/cameras/{camera_id}")

    assert response.status_code == 204
    assert not response.text


def test_delete_camera_not_found_returns_404(api_client, db_session_mock):
    camera_id = uuid.uuid4()
    db_session_mock.execute.return_value = scalar_one_result(None)

    response = api_client.delete(f"/api/v2/cameras/{camera_id}")

    assert response.status_code == 404
