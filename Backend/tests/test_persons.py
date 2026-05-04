from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from api_fakes import scalar_all_result, scalar_one_result


def _unknown_label(person_id: uuid.UUID) -> str:
    return f"Unknown Person {person_id.hex[:8].upper()}"


def test_list_unknown_persons_presigns_raw_snapshot_object_keys(api_client, db_session_mock, monkeypatch):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    observed_at = datetime(2026, 4, 3, 10, 15, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "api.routes.persons.get_presigned_url",
        lambda object_name: f"http://localhost:9000/airco-evidence/{object_name}?signed=1",
    )

    db_session_mock.execute.side_effect = [
        scalar_one_result(SimpleNamespace(id=session_id, tenant_id="default", name="Morning Shift", status="running")),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="CAM-08",
                    location="Restricted Wing",
                    zone="Restricted Zone",
                    rtsp_url="rtsp://cam-08",
                    is_entrance=False,
                    is_active=True,
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=person_id,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=None,
                    display_name="Unknown #1",
                    recognition_state="unknown",
                    current_cameras=[str(camera_id)],
                    face_confidence=0.41,
                    body_confidence=0.38,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url="snapshots/session/camera-1/frame.jpg",
                    first_seen_at=observed_at,
                    last_seen_at=observed_at + timedelta(seconds=30),
                    is_active=True,
                    evidence_summary={},
                )
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get(f"/api/v2/persons/unknown?session_id={session_id}")

    assert response.status_code == 200
    assert response.json()["persons"][0]["best_thumbnail_url"] == (
        "http://localhost:9000/airco-evidence/snapshots/session/camera-1/frame.jpg?signed=1"
    )


def test_list_unknown_persons_returns_metric_first_dashboard_payload(api_client, db_session_mock):
    session_id = uuid.uuid4()
    unknown_person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    observed_at = datetime(2026, 4, 3, 10, 15, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="CAM-08",
                    location="Restricted Wing",
                    zone="Restricted Zone",
                    rtsp_url="rtsp://cam-08",
                    is_entrance=False,
                    is_active=True,
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=unknown_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=None,
                    display_name="Unknown #1",
                    recognition_state="unknown",
                    current_cameras=[str(camera_id)],
                    face_confidence=0.41,
                    body_confidence=0.38,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url="minio://thumbs/unknown-1.jpg",
                    first_seen_at=observed_at,
                    last_seen_at=observed_at + timedelta(seconds=30),
                    is_active=True,
                    evidence_summary={"face_hits": 1, "body_matches": 0, "reviewed": False},
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=unknown_person_id,
                    alert_type="unknown_person",
                    severity="high",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Unknown person detected on camera",
                    status="active",
                    created_at=observed_at,
                )
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get(f"/api/v2/persons/unknown?session_id={session_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == str(session_id)
    assert payload["summary"] == {
        "unknown_persons": 1,
        "active_unknown_persons": 1,
        "active_alerts": 1,
        "high_risk_persons": 1,
        "average_face_confidence": 0.41,
    }
    assert len(payload["persons"]) == 1
    person = payload["persons"][0]
    assert person["person_id"] == str(unknown_person_id)
    assert person["display_name"] == "Unknown Visitor 01"
    assert person["current_camera"] == "CAM-08"
    assert person["current_zone"] == "Restricted Zone"
    assert person["best_thumbnail_url"] == "minio://thumbs/unknown-1.jpg"
    assert person["active_alert_types"] == ["unknown_person"]
    assert person["risk_level"] == "high"


def test_generic_person_endpoint_normalizes_unknown_display_name(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    observed_at = datetime(2026, 4, 3, 11, 0, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=person_id,
                session_id=session_id,
                tenant_id="default",
                employee_id=None,
                display_name="Unknown #99",
                recognition_state="unknown",
                is_active=True,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                face_confidence=0.22,
            )
        )
    ]

    response = api_client.get(f"/api/v2/persons/{person_id}")

    assert response.status_code == 200
    assert response.json()["display_name"] == _unknown_label(person_id)
    assert "Unknown #" not in response.json()["display_name"]


def test_list_unknown_persons_filters_people_from_previous_run_of_restarted_session(api_client, db_session_mock):
    session_id = uuid.uuid4()
    old_person_id = uuid.uuid4()
    current_person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    run_start = datetime(2026, 4, 19, 17, 45, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="again",
                status="running",
                created_at=datetime(2026, 4, 12, 14, 24, tzinfo=timezone.utc),
                started_at=run_start,
                stopped_at=datetime(2026, 4, 12, 15, 19, tzinfo=timezone.utc),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="CAM-08",
                    location="Restricted Wing",
                    zone="Restricted Zone",
                    rtsp_url="rtsp://cam-08",
                    is_entrance=False,
                    is_active=True,
                )
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=old_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=None,
                    display_name="Old Unknown",
                    recognition_state="unknown",
                    current_cameras=[str(camera_id)],
                    face_confidence=0.41,
                    body_confidence=0.38,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url=None,
                    first_seen_at=datetime(2026, 4, 12, 15, 10, tzinfo=timezone.utc),
                    last_seen_at=datetime(2026, 4, 12, 15, 18, tzinfo=timezone.utc),
                    is_active=False,
                    evidence_summary={},
                ),
                SimpleNamespace(
                    id=current_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=None,
                    display_name="Current Unknown",
                    recognition_state="unknown",
                    current_cameras=[str(camera_id)],
                    face_confidence=0.41,
                    body_confidence=0.38,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url=None,
                    first_seen_at=run_start + timedelta(seconds=5),
                    last_seen_at=run_start + timedelta(seconds=40),
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get(f"/api/v2/persons/unknown?session_id={session_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["unknown_persons"] == 1
    assert payload["persons"][0]["person_id"] == str(current_person_id)
