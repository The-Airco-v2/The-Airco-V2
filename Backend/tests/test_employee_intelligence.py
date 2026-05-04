"""Employee intelligence route contract tests for Frontend alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from api_fakes import rows_result, scalar_all_result, scalar_one_result
from api.routes.employee_intelligence import _employee_intelligence_response


def test_get_employee_intelligence_returns_canonical_payload_shape(api_client, db_session_mock):
    session_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    current_camera_id = uuid.uuid4()
    person_id = uuid.uuid4()

    first_seen_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)
    last_seen_at = datetime(2026, 3, 29, 13, 10, tzinfo=timezone.utc)
    entered_at = datetime(2026, 3, 29, 13, 2, tzinfo=timezone.utc)
    current_segment_entered_at = datetime(2026, 3, 29, 13, 7, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_all_result(
            [
                SimpleNamespace(
                    id=person_id,
                    employee_id=employee_id,
                    display_name="Guest User",
                    is_active=True,
                    first_seen_at=first_seen_at,
                    last_seen_at=last_seen_at,
                    current_cameras=[str(current_camera_id)],
                    recognition_state="recognized",
                    best_thumbnail_url="https://cdn.example.com/thumbnail.jpg",
                    face_confidence=0.91,
                )
            ]
        ),
        scalar_one_result(SimpleNamespace(id=employee_id, name="Nikhil")),
        rows_result([SimpleNamespace(camera_id=camera_id, total=120)]),
        scalar_one_result("North Wing"),
        rows_result(
            [
                (
                    SimpleNamespace(entered_at=entered_at),
                    "Lobby",
                ),
                (
                    SimpleNamespace(entered_at=current_segment_entered_at),
                    "Break Room",
                ),
            ]
        ),
        scalar_one_result(90.0),
        rows_result([("working", 5), ("idle", 3)]),
        scalar_one_result("walking"),
        scalar_one_result(1),
        scalar_one_result(
            SimpleNamespace(id=current_camera_id, name="Front Door", zone="Lobby")
        ),
    ]

    response = api_client.get(f"/api/v2/sessions/{session_id}/employee-intelligence")

    assert response.status_code == 200
    assert response.json() == {
        "employees": [
            {
                "employee_id": str(employee_id),
                "employee_name": "Nikhil",
                "training_status": "trained",
                "presence": {
                    "is_present": True,
                    "entered_at": "2026-03-29T13:00:00Z",
                    "last_seen": "2026-03-29T13:10:00Z",
                },
                "live_status": "walking",
                "location": {
                    "current_zone": "Lobby",
                    "current_camera": "Front Door",
                },
                "movement_path": [
                    {"zone": "Lobby", "time": "13:02:00"},
                    {"zone": "Break Room", "time": "13:07:00"},
                ],
                "productivity": {
                    "working_seconds": 5.0,
                    "idle_seconds": 3.0,
                    "productivity_percent": 62,
                },
                "dwell_analysis": {"North Wing": 120.0},
                "violations": {
                    "phone_usage_minutes": 1.5,
                    "phone_violation": True,
                    "restricted_zone_violation": True,
                },
                "recognition_state": "recognized",
                "best_thumbnail_url": "https://cdn.example.com/thumbnail.jpg",
                "confidence": 0.91,
            }
        ]
    }


def test_get_employee_intelligence_excludes_unknown_session_persons(api_client, db_session_mock):
    session_id = uuid.uuid4()
    employee_id = uuid.uuid4()

    db_session_mock.execute.side_effect = [
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    employee_id=None,
                    display_name="Unknown #67",
                    is_active=True,
                    first_seen_at=None,
                    last_seen_at=None,
                    current_cameras=[],
                    recognition_state="unknown",
                    best_thumbnail_url=None,
                    face_confidence=0.21,
                ),
                SimpleNamespace(
                    id=uuid.uuid4(),
                    employee_id=employee_id,
                    display_name="Nikhil",
                    is_active=True,
                    first_seen_at=None,
                    last_seen_at=None,
                    current_cameras=[],
                    recognition_state="identified",
                    best_thumbnail_url=None,
                    face_confidence=0.97,
                ),
            ]
        ),
        scalar_one_result(SimpleNamespace(id=employee_id, name="Nikhil")),
        rows_result([]),
        rows_result([]),
        scalar_one_result(0.0),
        rows_result([]),
        scalar_one_result("idle"),
        scalar_one_result(0),
    ]

    response = api_client.get(f"/api/v2/sessions/{session_id}/employee-intelligence")

    assert response.status_code == 200
    assert response.json() == {
        "employees": [
            {
                "employee_id": str(employee_id),
                "employee_name": "Nikhil",
                "training_status": "trained",
                "presence": {
                    "is_present": True,
                    "entered_at": None,
                    "last_seen": None,
                },
                "live_status": "idle",
                "location": {
                    "current_zone": None,
                    "current_camera": None,
                },
                "movement_path": [],
                "productivity": {
                    "working_seconds": 0.0,
                    "idle_seconds": 0.0,
                    "productivity_percent": 0,
                },
                "dwell_analysis": {},
                "violations": {
                    "phone_usage_minutes": 0.0,
                    "phone_violation": False,
                    "restricted_zone_violation": False,
                },
                "recognition_state": "identified",
                "best_thumbnail_url": None,
                "confidence": 0.97,
            }
        ]
    }


async def test_employee_intelligence_helper_returns_canonical_payload_shape(db_session_mock):
    employee_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    current_camera_id = uuid.uuid4()
    person_id = uuid.uuid4()

    first_seen_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)
    last_seen_at = datetime(2026, 3, 29, 13, 10, tzinfo=timezone.utc)
    entered_at = datetime(2026, 3, 29, 13, 2, tzinfo=timezone.utc)
    current_segment_entered_at = datetime(2026, 3, 29, 13, 7, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(SimpleNamespace(id=employee_id, name="Nikhil")),
        rows_result([SimpleNamespace(camera_id=camera_id, total=120)]),
        scalar_one_result("North Wing"),
        rows_result(
            [
                (
                    SimpleNamespace(entered_at=entered_at),
                    "Lobby",
                ),
                (
                    SimpleNamespace(entered_at=current_segment_entered_at),
                    "Break Room",
                ),
            ]
        ),
        scalar_one_result(90.0),
        rows_result([("working", 5), ("idle", 3)]),
        scalar_one_result("walking"),
        scalar_one_result(1),
        scalar_one_result(
            SimpleNamespace(id=current_camera_id, name="Front Door", zone="Lobby")
        ),
    ]

    payload = await _employee_intelligence_response(
        db_session_mock,
        SimpleNamespace(
            id=person_id,
            employee_id=employee_id,
            display_name="Guest User",
            is_active=True,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            current_cameras=[str(current_camera_id)],
            recognition_state="recognized",
            best_thumbnail_url="https://cdn.example.com/thumbnail.jpg",
            face_confidence=0.91,
        ),
    )

    assert payload.model_dump(mode="json") == {
        "employee_id": str(employee_id),
        "employee_name": "Nikhil",
        "training_status": "trained",
        "presence": {
            "is_present": True,
            "entered_at": "2026-03-29T13:00:00Z",
            "last_seen": "2026-03-29T13:10:00Z",
        },
        "live_status": "walking",
        "location": {
            "current_zone": "Lobby",
            "current_camera": "Front Door",
        },
        "movement_path": [
            {"zone": "Lobby", "time": "13:02:00"},
            {"zone": "Break Room", "time": "13:07:00"},
        ],
        "productivity": {
            "working_seconds": 5.0,
            "idle_seconds": 3.0,
            "productivity_percent": 62,
        },
        "dwell_analysis": {"North Wing": 120.0},
        "violations": {
            "phone_usage_minutes": 1.5,
            "phone_violation": True,
            "restricted_zone_violation": True,
        },
        "recognition_state": "recognized",
        "best_thumbnail_url": "https://cdn.example.com/thumbnail.jpg",
        "confidence": 0.91,
    }


async def test_employee_intelligence_helper_selects_current_camera_deterministically(db_session_mock):
    employee_id = uuid.uuid4()
    person_id = uuid.uuid4()
    current_camera_late = uuid.UUID(int=2)
    current_camera_early = uuid.UUID(int=1)

    db_session_mock.execute.side_effect = [
        scalar_one_result(SimpleNamespace(id=employee_id, name="Nikhil")),
        rows_result([]),
        rows_result([]),
        scalar_one_result(0.0),
        rows_result([]),
        scalar_one_result("idle"),
        scalar_one_result(0),
        scalar_one_result(
            SimpleNamespace(id=current_camera_early, name="Front Door", zone="Lobby")
        ),
    ]

    payload = await _employee_intelligence_response(
        db_session_mock,
        SimpleNamespace(
            id=person_id,
            employee_id=employee_id,
            display_name="Guest User",
            is_active=True,
            first_seen_at=None,
            last_seen_at=None,
            current_cameras=[str(current_camera_late), str(current_camera_early)],
            recognition_state="recognized",
            best_thumbnail_url=None,
            face_confidence=None,
        ),
    )

    assert payload.location.model_dump() == {
        "current_zone": "Lobby",
        "current_camera": "Front Door",
    }
    compiled_statement = str(
        db_session_mock.execute.await_args_list[-1].args[0].compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert current_camera_early.hex in compiled_statement


async def test_employee_intelligence_helper_keeps_dwell_analysis_keys_unique(db_session_mock):
    employee_id = uuid.uuid4()
    person_id = uuid.uuid4()
    first_camera_id = uuid.UUID(int=1)
    second_camera_id = uuid.UUID(int=2)

    db_session_mock.execute.side_effect = [
        scalar_one_result(SimpleNamespace(id=employee_id, name="Nikhil")),
        rows_result(
            [
                SimpleNamespace(camera_id=first_camera_id, total=120),
                SimpleNamespace(camera_id=second_camera_id, total=45),
            ]
        ),
        scalar_one_result("North Wing"),
        scalar_one_result("North Wing"),
        rows_result([]),
        scalar_one_result(0.0),
        rows_result([]),
        scalar_one_result("idle"),
        scalar_one_result(0),
    ]

    payload = await _employee_intelligence_response(
        db_session_mock,
        SimpleNamespace(
            id=person_id,
            employee_id=employee_id,
            display_name="Guest User",
            is_active=True,
            first_seen_at=None,
            last_seen_at=None,
            current_cameras=[],
            recognition_state="recognized",
            best_thumbnail_url=None,
            face_confidence=None,
        ),
    )

    assert payload.dwell_analysis == {
        "North Wing": 120.0,
        f"North Wing ({second_camera_id})": 45.0,
    }
