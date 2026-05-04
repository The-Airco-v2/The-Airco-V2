"""Contract test for the day-level office overview route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import uuid

from api_fakes import scalar_all_result, scalar_one_result
from api.routes.overview import (
    build_overview_payload,
    build_overview_patch_payload,
    build_overview_snapshot_payload,
)
from airco.events import build_live_event_envelope


OFFICE_TZ = ZoneInfo("Asia/Kolkata")


class ScalarOrScalarsResult:
    def __init__(self, scalar_value=None, items=None):
        self._scalar_value = scalar_value
        self._items = [] if items is None else items

    def scalar_one(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return self

    def all(self):
        return self._items


def _iso_local(dt: datetime) -> str:
    return dt.astimezone(OFFICE_TZ).isoformat()


def _freeze_overview_now(api_client, monkeypatch, frozen_now: datetime):
    route = next(
        (candidate for candidate in api_client.app.routes if getattr(candidate, "path", None) == "/api/v2/overview/today"),
        None,
    )
    if route is None:
        raise RuntimeError("overview route not loaded")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_now
            return frozen_now.astimezone(tz)

    monkeypatch.setitem(route.endpoint.__globals__, "datetime", FrozenDateTime)


def test_build_overview_payload_returns_canonical_rest_shape():
    session = SimpleNamespace(
        id=uuid.uuid4(),
        name="Morning Shift",
        status="running",
        mode="auto",
        created_at=datetime(2026, 3, 31, 4, 0, tzinfo=timezone.utc),
        started_at=datetime(2026, 3, 31, 4, 30, tzinfo=timezone.utc),
        stopped_at=None,
    )
    summary = {
        "counts": {
            "expected": 2,
            "present": 1,
            "late": 0,
            "absent": 0,
            "unknown": 1,
            "active_exceptions": 0,
        },
        "phone": {"violators": 0, "total_minutes": 0.0},
        "health": {
            "camera_total": 2,
            "camera_active": 2,
            "entrance_cameras": 1,
            "coverage_status": "healthy",
        },
    }
    employees = [
        {
            "employee_id": "employee-1",
            "employee_name": "Alice",
            "is_present": True,
            "attendance_status": "present",
            "current_zone": "Lobby",
            "current_camera": "Lobby Entrance",
            "last_seen": "2026-03-31T10:00:00+05:30",
            "recognition_state": "identified",
            "confidence": 0.98,
            "phone_usage_minutes": 0.0,
            "exception_badges": [],
        }
    ]

    assert build_overview_payload(session=session, summary=summary, employees=employees) == {
        "session": {
            "id": str(session.id),
            "name": "Morning Shift",
            "status": "running",
            "mode": "auto",
            "created_at": "2026-03-31T04:00:00+00:00",
            "started_at": "2026-03-31T04:30:00+00:00",
            "stopped_at": None,
        },
        "summary": summary,
        "employees": employees,
    }


def test_build_overview_snapshot_payload_wraps_canonical_payload_for_tenant_overview():
    overview_payload = {
        "session": None,
        "summary": {
            "counts": {
                "expected": 0,
                "present": 0,
                "late": 0,
                "absent": 0,
                "unknown": 0,
                "active_exceptions": 0,
            },
            "phone": {"violators": 0, "total_minutes": 0.0},
            "health": {
                "camera_total": 0,
                "camera_active": 0,
                "entrance_cameras": 0,
                "coverage_status": "healthy",
            },
        },
        "employees": [],
    }

    assert build_overview_snapshot_payload(
        tenant_id="tenant-7",
        session_id="session-9",
        occurred_at="2026-03-31T10:30:00Z",
        overview_payload=overview_payload,
    ) == build_live_event_envelope(
        event_type="overview.snapshot",
        tenant_id="tenant-7",
        session_id="session-9",
        occurred_at="2026-03-31T10:30:00Z",
        payload=overview_payload,
    )


def test_build_overview_patch_payload_wraps_minimal_patch_for_tenant_overview():
    patch_payload = {
        "summary": {
            "counts": {
                "expected": 2,
                "present": 2,
                "late": 0,
                "absent": 0,
                "unknown": 0,
                "active_exceptions": 0,
            }
        }
    }

    assert build_overview_patch_payload(
        tenant_id="tenant-7",
        session_id="session-9",
        occurred_at="2026-03-31T10:30:00Z",
        patch_payload=patch_payload,
    ) == build_live_event_envelope(
        event_type="overview.patch",
        tenant_id="tenant-7",
        session_id="session-9",
        occurred_at="2026-03-31T10:30:00Z",
        payload=patch_payload,
    )


def test_today_overview_returns_no_session_when_only_previous_day_session_exists(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    previous_session = SimpleNamespace(
        id=uuid.uuid4(),
        name="Yesterday Shift",
        status="stopped",
        started_at=frozen_now - timedelta(days=1, hours=2),
        stopped_at=frozen_now - timedelta(days=1, hours=1),
        created_at=frozen_now - timedelta(days=1, hours=3),
    )

    db_session_mock.execute.side_effect = [
        scalar_one_result(None),
        scalar_one_result(previous_session),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"] is None
    assert payload["summary"]["counts"] == {
        "expected": 0,
        "present": 0,
        "late": 0,
        "absent": 0,
        "unknown": 0,
        "active_exceptions": 0,
    }
    assert payload["employees"] == []


def test_today_overview_filters_people_and_events_from_previous_run_of_restarted_session(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 4, 19, 23, 25, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    current_person_id = uuid.uuid4()
    old_person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    run_start = datetime(2026, 4, 19, 17, 45, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                name="again",
                tenant_id="default",
                status="running",
                started_at=run_start,
                stopped_at=datetime(2026, 4, 12, 15, 19, tzinfo=timezone.utc),
                created_at=datetime(2026, 4, 12, 14, 24, tzinfo=timezone.utc),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="CAM-08",
                    zone="Lobby",
                    is_active=True,
                    is_entrance=True,
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=employee_id,
                    tenant_id="default",
                    name="Alice",
                    status="active",
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=old_person_id,
                    session_id=session_id,
                    employee_id=None,
                    recognition_state="unknown",
                    first_seen_at=datetime(2026, 4, 12, 15, 10, tzinfo=timezone.utc),
                    last_seen_at=datetime(2026, 4, 12, 15, 18, tzinfo=timezone.utc),
                    current_cameras=[str(camera_id)],
                    is_active=False,
                ),
                SimpleNamespace(
                    id=current_person_id,
                    session_id=session_id,
                    employee_id=employee_id,
                    recognition_state="identified",
                    first_seen_at=run_start + timedelta(minutes=1),
                    last_seen_at=run_start + timedelta(minutes=5),
                    current_cameras=[str(camera_id)],
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    session_id=session_id,
                    employee_id=employee_id,
                    session_person_id=current_person_id,
                    event_type="check_in",
                    time=run_start + timedelta(minutes=1),
                    camera_id=camera_id,
                ),
                SimpleNamespace(
                    session_id=session_id,
                    employee_id=employee_id,
                    session_person_id=current_person_id,
                    event_type="check_in",
                    time=datetime(2026, 4, 12, 15, 0, tzinfo=timezone.utc),
                    camera_id=camera_id,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"]["unknown"] == 0


def test_today_overview_ignores_check_out_only_attendance_before_absent_cutoff(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 10, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    bob_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()

    alice_seen = frozen_now.replace(hour=10, minute=5, second=0, microsecond=0)
    bob_checkout = frozen_now.replace(hour=10, minute=0, second=0, microsecond=0)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Morning Shift",
                status="running",
                started_at=alice_seen - timedelta(hours=1),
                stopped_at=None,
                created_at=alice_seen - timedelta(hours=2),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
                SimpleNamespace(
                    id=bob_id,
                    tenant_id="default",
                    name="Bob",
                    employee_code="E002",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen + timedelta(minutes=15),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_seen,
                    session_id=running_session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_in",
                    confidence=0.95,
                ),
                SimpleNamespace(
                    time=bob_checkout,
                    session_id=running_session_id,
                    tenant_id="default",
                    employee_id=bob_id,
                    session_person_id=bob_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_out",
                    confidence=0.95,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"] == {
        "expected": 2,
        "present": 1,
        "late": 1,
        "absent": 0,
        "unknown": 0,
        "active_exceptions": 1,
    }
    assert payload["employees"][1]["attendance_status"] == "unknown"
    assert payload["employees"][1]["is_present"] is False


def test_today_overview_marks_active_presence_after_hours_when_still_active(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 20, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    alice_person_id = uuid.uuid4()

    alice_seen = frozen_now.replace(hour=19, minute=55, second=0, microsecond=0)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Evening Shift",
                status="running",
                started_at=alice_seen - timedelta(hours=1),
                stopped_at=None,
                created_at=alice_seen - timedelta(hours=2),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"]["active_exceptions"] == 1
    assert payload["summary"]["counts"]["absent"] == 1
    assert payload["employees"][0]["exception_badges"] == ["absent", "after_hours"]


def test_today_overview_marks_active_presence_after_absent_cutoff_as_absent(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 11, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    alice_person_id = uuid.uuid4()

    first_seen = frozen_now.replace(hour=11, minute=5, second=0, microsecond=0)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Late Shift",
                status="running",
                started_at=first_seen,
                stopped_at=None,
                created_at=first_seen - timedelta(hours=1),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=first_seen,
                    last_seen_at=first_seen + timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"]["absent"] == 1
    assert payload["summary"]["counts"]["active_exceptions"] == 1
    assert payload["employees"][0]["attendance_status"] == "absent"
    assert payload["employees"][0]["exception_badges"] == ["absent"]


def test_today_overview_does_not_count_absent_first_seen_people_as_present(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 11, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    alice_person_id = uuid.uuid4()

    first_seen = frozen_now.replace(hour=11, minute=5, second=0, microsecond=0)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Late Shift",
                status="running",
                started_at=first_seen,
                stopped_at=None,
                created_at=first_seen - timedelta(hours=1),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=first_seen,
                    last_seen_at=first_seen + timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"] == {
        "expected": 1,
        "present": 0,
        "late": 0,
        "absent": 1,
        "unknown": 0,
        "active_exceptions": 1,
    }
    assert payload["employees"][0]["attendance_status"] == "absent"
    assert payload["employees"][0]["is_present"] is False


def test_today_overview_is_deterministic_for_multiple_person_rows_and_check_ins(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    cam_a = uuid.uuid4()
    cam_b = uuid.uuid4()
    newer_person_id = uuid.uuid4()
    older_person_id = uuid.uuid4()

    older_seen = frozen_now.replace(hour=9, minute=0, second=0, microsecond=0)
    newer_seen = frozen_now.replace(hour=9, minute=40, second=0, microsecond=0)
    older_check_in = older_seen + timedelta(minutes=5)
    newer_check_in = newer_seen + timedelta(minutes=5)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Morning Shift",
                status="running",
                started_at=older_seen,
                stopped_at=None,
                created_at=older_seen - timedelta(hours=1),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_a,
                    tenant_id="default",
                    name="Camera A",
                    location="Zone A",
                    zone="Zone A",
                    rtsp_url="rtsp://a",
                    is_entrance=True,
                    is_active=True,
                ),
                SimpleNamespace(
                    id=cam_b,
                    tenant_id="default",
                    name="Camera B",
                    location="Zone B",
                    zone="Zone B",
                    rtsp_url="rtsp://b",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=newer_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice New",
                    current_cameras=[str(cam_b)],
                    active_track_bindings={},
                    face_confidence=0.93,
                    body_confidence=0.86,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=newer_seen,
                    last_seen_at=newer_seen + timedelta(minutes=10),
                    is_active=True,
                    evidence_summary={},
                ),
                SimpleNamespace(
                    id=older_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice Old",
                    current_cameras=[str(cam_a)],
                    active_track_bindings={},
                    face_confidence=0.41,
                    body_confidence=0.34,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=older_seen,
                    last_seen_at=older_seen + timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=newer_check_in,
                    session_id=running_session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=newer_person_id,
                    camera_id=cam_b,
                    event_type="check_in",
                    confidence=0.97,
                ),
                SimpleNamespace(
                    time=older_check_in,
                    session_id=running_session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=older_person_id,
                    camera_id=cam_a,
                    event_type="check_in",
                    confidence=0.61,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["employees"] == [
        {
            "employee_id": str(alice_id),
            "employee_name": "Alice",
            "is_present": True,
            "attendance_status": "present",
            "current_zone": "Zone B",
            "current_camera": "Camera B",
            "last_seen": _iso_local(newer_seen + timedelta(minutes=10)),
            "recognition_state": "identified",
            "confidence": 0.93,
            "phone_usage_minutes": 0.0,
            "exception_badges": [],
        }
    ]


def test_today_overview_flags_active_presence_first_seen_before_7am(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 10, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    alice_person_id = uuid.uuid4()

    first_seen = frozen_now.replace(hour=6, minute=30, second=0, microsecond=0)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Night Shift",
                status="running",
                started_at=first_seen,
                stopped_at=None,
                created_at=first_seen - timedelta(hours=1),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=first_seen,
                    last_seen_at=first_seen + timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"]["active_exceptions"] == 1
    assert payload["employees"][0]["exception_badges"] == ["after_hours"]


def test_today_overview_keeps_cross_midnight_running_session_in_today(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 0, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    running_session = SimpleNamespace(
        id=uuid.uuid4(),
        name="Overnight Shift",
        status="running",
        started_at=datetime(2026, 3, 20, 23, 50, tzinfo=OFFICE_TZ),
        stopped_at=None,
        created_at=datetime(2026, 3, 20, 23, 45, tzinfo=OFFICE_TZ),
    )

    db_session_mock.execute.side_effect = [
        scalar_one_result(running_session),
        ScalarOrScalarsResult(scalar_value=running_session, items=[]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"] == {
        "id": str(running_session.id),
        "name": "Overnight Shift",
        "status": "running",
        "mode": None,
        "created_at": running_session.created_at.isoformat(),
        "started_at": running_session.started_at.isoformat(),
        "stopped_at": None,
    }
    assert payload["summary"]["counts"] == {
        "expected": 0,
        "present": 0,
        "late": 0,
        "absent": 0,
        "unknown": 0,
        "active_exceptions": 0,
    }
    assert payload["employees"] == []


def test_today_overview_filters_phone_usage_to_current_office_day(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 10, 0, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    overnight_session = SimpleNamespace(
        id=session_id,
        name="Overnight Shift",
        status="stopped",
        started_at=datetime(2026, 3, 20, 23, 55, tzinfo=OFFICE_TZ),
        stopped_at=datetime(2026, 3, 21, 9, 45, tzinfo=OFFICE_TZ),
        created_at=datetime(2026, 3, 20, 23, 50, tzinfo=OFFICE_TZ),
    )
    alice_seen = datetime(2026, 3, 21, 9, 10, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(None),
        scalar_one_result(overnight_session),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen + timedelta(minutes=30),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_seen,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_in",
                    confidence=0.95,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=datetime(2026, 3, 20, 23, 58, tzinfo=OFFICE_TZ),
                    session_id=session_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    track_id=1,
                    confidence=0.93,
                    duration_seconds=120.0,
                ),
                SimpleNamespace(
                    time=datetime(2026, 3, 21, 9, 20, tzinfo=OFFICE_TZ),
                    session_id=session_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    track_id=2,
                    confidence=0.94,
                    duration_seconds=20.0,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["phone"] == {
        "violators": 0,
        "total_minutes": 0.3,
    }
    assert payload["employees"] == [
        {
            "employee_id": str(alice_id),
            "employee_name": "Alice",
            "is_present": True,
            "attendance_status": "present",
            "current_zone": "Lobby",
            "current_camera": "Lobby Entrance",
            "last_seen": _iso_local(alice_seen + timedelta(minutes=30)),
            "recognition_state": "identified",
            "confidence": 0.98,
            "phone_usage_minutes": 0.3,
            "exception_badges": [],
        }
    ]


def test_today_overview_uses_current_office_day_for_cross_midnight_attendance_status(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 11, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()

    first_seen_previous_day = datetime(2026, 3, 20, 23, 50, tzinfo=OFFICE_TZ)
    last_seen_today = datetime(2026, 3, 21, 11, 15, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                name="Overnight Shift",
                status="running",
                started_at=first_seen_previous_day,
                stopped_at=None,
                created_at=first_seen_previous_day - timedelta(minutes=5),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=first_seen_previous_day,
                    last_seen_at=last_seen_today,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"] == {
        "expected": 1,
        "present": 1,
        "late": 0,
        "absent": 0,
        "unknown": 0,
        "active_exceptions": 1,
    }
    assert payload["employees"] == [
        {
            "employee_id": str(alice_id),
            "employee_name": "Alice",
            "is_present": True,
            "attendance_status": "present",
            "current_zone": "Lobby",
            "current_camera": "Lobby Entrance",
            "last_seen": _iso_local(last_seen_today),
            "recognition_state": "identified",
            "confidence": 0.98,
            "phone_usage_minutes": 0.0,
            "exception_badges": ["after_hours"],
        }
    ]


def test_today_overview_selects_valid_same_day_session_when_newer_candidates_do_not_overlap(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    valid_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()

    valid_session = SimpleNamespace(
        id=valid_session_id,
        name="Valid Morning Shift",
        status="stopped",
        started_at=datetime(2026, 3, 21, 9, 0, tzinfo=OFFICE_TZ),
        stopped_at=datetime(2026, 3, 21, 12, 0, tzinfo=OFFICE_TZ),
        created_at=datetime(2026, 3, 21, 8, 50, tzinfo=OFFICE_TZ),
    )
    newer_invalid_running = SimpleNamespace(
        id=uuid.uuid4(),
        name="Tomorrow Prep",
        status="running",
        started_at=datetime(2026, 3, 22, 0, 30, tzinfo=OFFICE_TZ),
        stopped_at=None,
        created_at=datetime(2026, 3, 22, 0, 31, tzinfo=OFFICE_TZ),
    )
    newest_invalid_overall = SimpleNamespace(
        id=uuid.uuid4(),
        name="Yesterday Archive",
        status="stopped",
        started_at=datetime(2026, 3, 22, 1, 0, tzinfo=OFFICE_TZ),
        stopped_at=datetime(2026, 3, 22, 2, 0, tzinfo=OFFICE_TZ),
        created_at=datetime(2026, 3, 22, 2, 5, tzinfo=OFFICE_TZ),
    )
    alice_seen = datetime(2026, 3, 21, 9, 5, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        ScalarOrScalarsResult(
            scalar_value=newer_invalid_running,
            items=[newer_invalid_running],
        ),
        ScalarOrScalarsResult(
            scalar_value=newest_invalid_overall,
            items=[newest_invalid_overall, valid_session],
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=valid_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen + timedelta(minutes=10),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_seen,
                    session_id=valid_session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_in",
                    confidence=0.95,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["id"] == str(valid_session_id)
    assert payload["session"]["name"] == "Valid Morning Shift"
    assert payload["summary"]["counts"]["present"] == 1
    assert payload["employees"][0]["attendance_status"] == "present"


def test_today_overview_ignores_created_but_not_started_sessions_when_selecting_today_session(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    valid_session_id = uuid.uuid4()
    unstarted_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()

    valid_session = SimpleNamespace(
        id=valid_session_id,
        name="Valid Morning Shift",
        status="stopped",
        started_at=datetime(2026, 3, 21, 9, 0, tzinfo=OFFICE_TZ),
        stopped_at=datetime(2026, 3, 21, 12, 0, tzinfo=OFFICE_TZ),
        created_at=datetime(2026, 3, 21, 8, 50, tzinfo=OFFICE_TZ),
    )
    unstarted_session = SimpleNamespace(
        id=unstarted_session_id,
        name="Draft Afternoon Shift",
        status="running",
        started_at=None,
        stopped_at=None,
        created_at=datetime(2026, 3, 21, 13, 30, tzinfo=OFFICE_TZ),
    )
    alice_seen = datetime(2026, 3, 21, 9, 5, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        ScalarOrScalarsResult(
            scalar_value=unstarted_session,
            items=[unstarted_session],
        ),
        ScalarOrScalarsResult(
            scalar_value=valid_session,
            items=[unstarted_session, valid_session],
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=valid_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen + timedelta(minutes=10),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["id"] == str(valid_session_id)
    assert payload["session"]["name"] == "Valid Morning Shift"
    assert payload["summary"]["counts"]["present"] == 1
    assert payload["employees"][0]["attendance_status"] == "present"


def test_today_overview_rounds_summary_phone_total_from_raw_seconds(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    bob_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    alice_seen = datetime(2026, 3, 21, 9, 5, tzinfo=OFFICE_TZ)
    bob_seen = datetime(2026, 3, 21, 9, 10, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                name="Morning Shift",
                status="running",
                started_at=alice_seen - timedelta(minutes=30),
                stopped_at=None,
                created_at=alice_seen - timedelta(hours=1),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
                SimpleNamespace(
                    id=bob_id,
                    tenant_id="default",
                    name="Bob",
                    employee_code="E002",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen + timedelta(minutes=15),
                    is_active=True,
                    evidence_summary={},
                ),
                SimpleNamespace(
                    id=bob_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=bob_id,
                    display_name="Bob",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.91,
                    body_confidence=0.82,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=bob_seen,
                    last_seen_at=bob_seen + timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_seen,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_in",
                    confidence=0.95,
                ),
                SimpleNamespace(
                    time=bob_seen,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=bob_id,
                    session_person_id=bob_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_in",
                    confidence=0.94,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_seen + timedelta(minutes=30),
                    session_id=session_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    track_id=1,
                    confidence=0.93,
                    duration_seconds=20.0,
                ),
                SimpleNamespace(
                    time=bob_seen + timedelta(minutes=25),
                    session_id=session_id,
                    session_person_id=bob_person_id,
                    camera_id=cam_entrance_active,
                    track_id=2,
                    confidence=0.92,
                    duration_seconds=20.0,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["phone"] == {
        "violators": 0,
        "total_minutes": 0.7,
    }
    assert payload["employees"] == [
        {
            "employee_id": str(alice_id),
            "employee_name": "Alice",
            "is_present": True,
            "attendance_status": "present",
            "current_zone": "Lobby",
            "current_camera": "Lobby Entrance",
            "last_seen": _iso_local(alice_seen + timedelta(minutes=15)),
            "recognition_state": "identified",
            "confidence": 0.98,
            "phone_usage_minutes": 0.3,
            "exception_badges": [],
        },
        {
            "employee_id": str(bob_id),
            "employee_name": "Bob",
            "is_present": True,
            "attendance_status": "present",
            "current_zone": "Lobby",
            "current_camera": "Lobby Entrance",
            "last_seen": _iso_local(bob_seen + timedelta(minutes=5)),
            "recognition_state": "identified",
            "confidence": 0.91,
            "phone_usage_minutes": 0.3,
            "exception_badges": [],
        },
    ]


def test_today_overview_counts_unknown_session_persons_not_employee_attendance_unknowns(
    api_client, db_session_mock, monkeypatch
):
    frozen_now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    tenant_id = uuid.uuid4()
    running_session_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    unknown_person_id = uuid.uuid4()

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                tenant_id=tenant_id,
                name="Morning Shift",
                status="running",
                mode="auto",
                created_at=frozen_now - timedelta(hours=2),
                started_at=frozen_now - timedelta(hours=2),
                stopped_at=None,
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=employee_id,
                    tenant_id=tenant_id,
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=unknown_person_id,
                    session_id=running_session_id,
                    tenant_id=tenant_id,
                    recognition_state="unknown",
                    employee_id=None,
                    display_name="Unknown",
                    current_cameras=[],
                    active_track_bindings={},
                    face_confidence=0.41,
                    body_confidence=0.0,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=frozen_now - timedelta(minutes=20),
                    last_seen_at=frozen_now - timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                )
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"]["expected"] == 1
    assert payload["summary"]["counts"]["present"] == 0
    assert payload["summary"]["counts"]["absent"] == 1
    assert payload["summary"]["counts"]["unknown"] == 1


def test_today_overview_hides_stale_previous_day_person_metadata(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    previous_day_seen = datetime(2026, 3, 20, 23, 50, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                name="Overnight Shift",
                status="running",
                started_at=previous_day_seen - timedelta(minutes=5),
                stopped_at=None,
                created_at=previous_day_seen - timedelta(minutes=10),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=previous_day_seen,
                    last_seen_at=previous_day_seen + timedelta(minutes=5),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["counts"] == {
        "expected": 1,
        "present": 0,
        "late": 0,
        "absent": 1,
        "unknown": 0,
        "active_exceptions": 1,
    }
    assert payload["employees"] == [
        {
            "employee_id": str(alice_id),
            "employee_name": "Alice",
            "is_present": False,
            "attendance_status": "absent",
            "current_zone": None,
            "current_camera": None,
            "last_seen": None,
            "recognition_state": "unknown",
            "confidence": 0.0,
            "phone_usage_minutes": 0.0,
            "exception_badges": ["absent"],
        }
    ]


def test_today_overview_returns_day_level_summary(api_client, db_session_mock, monkeypatch):
    running_session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    alice_person_id = uuid.uuid4()
    cam_entrance_active = uuid.uuid4()
    cam_entrance_inactive = uuid.uuid4()

    frozen_now = datetime(2026, 3, 21, 14, 30, tzinfo=OFFICE_TZ)
    _freeze_overview_now(api_client, monkeypatch, frozen_now)

    now_local = frozen_now
    alice_seen = now_local.replace(hour=10, minute=5, second=0, microsecond=0)
    alice_check_in = alice_seen

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=running_session_id,
                name="Morning Shift",
                status="running",
                started_at=alice_seen - timedelta(hours=1),
                stopped_at=None,
                created_at=alice_seen - timedelta(hours=2),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=cam_entrance_active,
                    tenant_id="default",
                    name="Lobby Entrance",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
                SimpleNamespace(
                    id=cam_entrance_inactive,
                    tenant_id="default",
                    name="Side Door",
                    location="Side Door",
                    zone="Side",
                    rtsp_url="rtsp://side",
                    is_entrance=True,
                    is_active=False,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_id,
                    tenant_id="default",
                    name="Alice",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
                SimpleNamespace(
                    id=bob_id,
                    tenant_id="default",
                    name="Bob",
                    employee_code="E002",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alice_person_id,
                    session_id=running_session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=alice_id,
                    display_name="Alice",
                    current_cameras=[str(cam_entrance_active)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.85,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=alice_seen,
                    last_seen_at=alice_seen + timedelta(minutes=15),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_check_in,
                    session_id=running_session_id,
                    tenant_id="default",
                    employee_id=alice_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    event_type="check_in",
                    confidence=0.95,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alice_seen + timedelta(minutes=30),
                    session_id=running_session_id,
                    session_person_id=alice_person_id,
                    camera_id=cam_entrance_active,
                    track_id=1,
                    confidence=0.93,
                    duration_seconds=60.0,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/overview/today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["id"] == str(running_session_id)
    assert payload["session"]["status"] == "running"
    assert payload["summary"]["counts"] == {
        "expected": 2,
        "present": 1,
        "late": 1,
        "absent": 1,
        "unknown": 0,
        "active_exceptions": 2,
    }
    assert payload["summary"]["phone"] == {
        "violators": 1,
        "total_minutes": 1.0,
    }
    assert payload["summary"]["health"] == {
        "camera_total": 2,
        "camera_active": 1,
        "entrance_cameras": 2,
        "coverage_status": "degraded",
    }
    assert payload["employees"] == [
        {
            "employee_id": str(alice_id),
            "employee_name": "Alice",
            "is_present": True,
            "attendance_status": "late",
            "current_zone": "Lobby",
            "current_camera": "Lobby Entrance",
            "last_seen": _iso_local(alice_seen + timedelta(minutes=15)),
            "recognition_state": "identified",
            "confidence": 0.98,
            "phone_usage_minutes": 1.0,
            "exception_badges": ["late", "phone_violation"],
        },
        {
            "employee_id": str(bob_id),
            "employee_name": "Bob",
            "is_present": False,
            "attendance_status": "absent",
            "current_zone": None,
            "current_camera": None,
            "last_seen": None,
            "recognition_state": "unknown",
            "confidence": 0.0,
            "phone_usage_minutes": 0.0,
            "exception_badges": ["absent"],
        },
    ]
