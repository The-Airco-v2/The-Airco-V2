"""Contract tests for the dashboard-facing unknown-person API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from api_fakes import scalar_all_result, scalar_one_result


def _unknown_label(order: int) -> str:
    return f"Unknown Visitor {order:02d}"


def test_unknown_persons_list_returns_dashboard_summary_and_table_shape(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    alert_time = datetime(2026, 3, 29, 13, 5, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                created_at=datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc),
                started_at=datetime(2026, 3, 29, 12, 15, tzinfo=timezone.utc),
                stopped_at=None,
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="Lobby Cam",
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
                    id=person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="unknown",
                    employee_id=None,
                    display_name="Unknown #7",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.31,
                    body_confidence=0.44,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url=None,
                    first_seen_at=alert_time,
                    last_seen_at=alert_time + timedelta(seconds=30),
                    is_active=True,
                    evidence_summary={"face_hits": 1, "body_matches": 0, "reviewed": False},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=person_id,
                    alert_type="unknown_person",
                    severity="medium",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Unknown person detected on camera",
                    status="active",
                    dedup_key="unknown:session-person",
                    created_at=alert_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/persons/unknown", params={"session_id": str(session_id)})

    assert response.status_code == 200
    payload = response.json()

    assert payload["session_id"] == str(session_id)
    assert payload["summary"]["unknown_persons"] == 1
    assert payload["summary"]["active_unknown_persons"] == 1
    assert payload["summary"]["active_alerts"] == 1
    assert payload["summary"]["high_risk_persons"] == 1
    assert payload["persons"][0]["person_id"] == str(person_id)
    assert payload["persons"][0]["display_name"] == _unknown_label(1)


def test_unknown_person_detail_returns_timeline_storyboard_dwell_and_risk_context(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    first_seen_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)
    entered_at = datetime(2026, 3, 29, 13, 1, tzinfo=timezone.utc)
    alert_time = datetime(2026, 3, 29, 13, 5, tzinfo=timezone.utc)
    latest_seen_at = datetime(2026, 3, 29, 13, 6, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                created_at=datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc),
                started_at=datetime(2026, 3, 29, 12, 15, tzinfo=timezone.utc),
                stopped_at=None,
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="Lobby Cam",
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
                    id=person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="unknown",
                    employee_id=None,
                    display_name="Unknown #7",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.31,
                    body_confidence=0.44,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url="https://cdn.example.com/unknown-7.jpg",
                    first_seen_at=first_seen_at,
                    last_seen_at=latest_seen_at,
                    is_active=True,
                    evidence_summary={"face_hits": 1, "body_matches": 0, "reviewed": False},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=person_id,
                    alert_type="unknown_person",
                    severity="medium",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Unknown person detected on camera",
                    status="active",
                    dedup_key="unknown:session-person",
                    created_at=alert_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    entered_at=entered_at,
                    exited_at=alert_time,
                    dwell_seconds=240.0,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alert_time,
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    track_id=42,
                    confidence=0.94,
                    duration_seconds=45.0,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    event_type="identity",
                    full_frame_url="https://cdn.example.com/unknown-7-first.jpg",
                    face_crop_url=None,
                    body_crop_url=None,
                    bbox=None,
                    score=0.91,
                    created_at=first_seen_at,
                ),
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    event_type="periodic",
                    full_frame_url="https://cdn.example.com/unknown-7-last.jpg",
                    face_crop_url=None,
                    body_crop_url=None,
                    bbox=None,
                    score=0.89,
                    created_at=latest_seen_at,
                ),
            ]
        ),
    ]

    response = api_client.get(
        f"/api/v2/persons/unknown/{person_id}",
        params={"session_id": str(session_id)},
    )

    assert response.status_code == 200
    payload = response.json()
    person = payload["person"]
    timeline = payload["timeline"]
    storyboard = payload["storyboard"]

    assert payload["session_id"] == str(session_id)
    assert person["person_id"] == str(person_id)
    assert person["display_name"] == _unknown_label(1)
    assert isinstance(person["continuity_confidence"], (int, float))
    assert 0.0 <= person["continuity_confidence"] <= 1.0
    assert isinstance(person["continuity_reasons"], list)
    assert person["continuity_reasons"]
    assert all(isinstance(reason, str) and reason for reason in person["continuity_reasons"])
    assert set(timeline.keys()) == {"window_start", "window_end", "moments"}
    assert timeline["window_start"] == "2026-03-29T13:00:00Z"
    assert timeline["window_end"] == "2026-03-29T13:06:00Z"
    assert isinstance(timeline["moments"], list)
    assert timeline["moments"]
    assert [moment["occurred_at"] for moment in timeline["moments"]] == sorted(
        moment["occurred_at"] for moment in timeline["moments"]
    )
    assert any(moment["kind"] == "alert_backed" for moment in timeline["moments"])
    assert timeline["moments"][-1]["kind"] == "latest_seen"
    assert isinstance(storyboard, list)
    assert storyboard
    assert len(storyboard) < len(timeline["moments"])
    assert {item["id"] for item in storyboard}.issubset({moment["id"] for moment in timeline["moments"]})
    assert [item["id"] for item in storyboard] != [moment["id"] for moment in timeline["moments"]]


def test_unknown_person_detail_requires_explicit_session_scope(api_client):
    person_id = uuid.uuid4()

    response = api_client.get(f"/api/v2/persons/unknown/{person_id}")

    assert response.status_code == 422
    assert "session_id" in str(response.json()["detail"])


def test_unknown_person_detail_returns_evidence_timeline_and_storyboard(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    later_person_id = uuid.uuid4()
    entry_camera_id = uuid.uuid4()
    hall_camera_id = uuid.uuid4()
    first_seen_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)
    dwell_checkpoint_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)
    transition_at = datetime(2026, 3, 29, 13, 3, tzinfo=timezone.utc)
    alert_time = datetime(2026, 3, 29, 13, 5, tzinfo=timezone.utc)
    latest_seen_at = datetime(2026, 3, 29, 13, 12, tzinfo=timezone.utc)
    later_first_seen_at = datetime(2026, 3, 29, 13, 20, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                created_at=datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc),
                started_at=datetime(2026, 3, 29, 12, 15, tzinfo=timezone.utc),
                stopped_at=None,
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=entry_camera_id,
                    tenant_id="default",
                    name="Entry Cam",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
                SimpleNamespace(
                    id=hall_camera_id,
                    tenant_id="default",
                    name="South Hall Cam",
                    location="South Hall",
                    zone="South Hall",
                    rtsp_url="rtsp://south-hall",
                    is_entrance=False,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="unknown",
                    employee_id=None,
                    display_name="Unknown #7",
                    current_cameras=[str(hall_camera_id)],
                    active_track_bindings={},
                    face_confidence=0.31,
                    body_confidence=0.44,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url="https://cdn.example.com/unknown-7.jpg",
                    first_seen_at=first_seen_at,
                    last_seen_at=latest_seen_at,
                    is_active=True,
                    evidence_summary={"face_hits": 1, "body_matches": 0, "reviewed": False},
                ),
                SimpleNamespace(
                    id=later_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="unknown",
                    employee_id=None,
                    display_name="Unknown #9",
                    current_cameras=[str(entry_camera_id)],
                    active_track_bindings={},
                    face_confidence=0.62,
                    body_confidence=0.58,
                    identity_conflict=False,
                    merged_into_session_person_id=None,
                    best_thumbnail_url=None,
                    first_seen_at=later_first_seen_at,
                    last_seen_at=later_first_seen_at + timedelta(seconds=30),
                    is_active=True,
                    evidence_summary={"face_hits": 0, "body_matches": 1, "reviewed": False},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=person_id,
                    alert_type="unknown_person",
                    severity="medium",
                    camera_id=hall_camera_id,
                    evidence_url="https://cdn.example.com/unknown-7-alert.jpg",
                    message="Unknown person detected on camera",
                    status="active",
                    dedup_key="unknown:session-person",
                    created_at=alert_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=entry_camera_id,
                    entered_at=dwell_checkpoint_at,
                    exited_at=alert_time,
                    dwell_seconds=240.0,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=alert_time,
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=hall_camera_id,
                    track_id=42,
                    confidence=0.94,
                    duration_seconds=45.0,
                ),
                SimpleNamespace(
                    time=latest_seen_at,
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=hall_camera_id,
                    track_id=84,
                    confidence=0.89,
                    duration_seconds=30.0,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=entry_camera_id,
                    event_type="identity",
                    full_frame_url="https://cdn.example.com/unknown-7-first.jpg",
                    face_crop_url=None,
                    body_crop_url=None,
                    bbox=None,
                    score=0.91,
                    created_at=first_seen_at,
                ),
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=hall_camera_id,
                    event_type="periodic",
                    full_frame_url="https://cdn.example.com/unknown-7-transition.jpg",
                    face_crop_url=None,
                    body_crop_url=None,
                    bbox=None,
                    score=0.89,
                    created_at=transition_at,
                ),
                SimpleNamespace(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=hall_camera_id,
                    event_type="periodic",
                    full_frame_url="https://cdn.example.com/unknown-7-last.jpg",
                    face_crop_url=None,
                    body_crop_url=None,
                    bbox=None,
                    score=0.88,
                    created_at=latest_seen_at,
                ),
            ]
        ),
    ]

    response = api_client.get(
        f"/api/v2/persons/unknown/{person_id}",
        params={"session_id": str(session_id)},
    )

    assert response.status_code == 200
    payload = response.json()
    person = payload["person"]
    timeline = payload["timeline"]
    moment_times = [moment["occurred_at"] for moment in timeline["moments"]]
    storyboard_ids = [item["id"] for item in payload["storyboard"]]
    timeline_ids = [moment["id"] for moment in timeline["moments"]]

    assert payload["session_id"] == str(session_id)
    assert person["person_id"] == str(person_id)
    assert person["display_name"] == _unknown_label(1)
    assert isinstance(person["continuity_confidence"], (int, float))
    assert 0.0 <= person["continuity_confidence"] <= 1.0
    assert isinstance(person["continuity_reasons"], list)
    assert person["continuity_reasons"]

    assert timeline["window_start"] == "2026-03-29T13:00:00Z"
    assert timeline["window_end"] == "2026-03-29T13:12:00Z"
    assert moment_times == sorted(moment_times)
    assert timeline["moments"][0]["kind"] == "first_seen"
    assert sum(1 for moment in timeline["moments"] if moment["kind"] == "dwell_checkpoint") >= 4
    assert any(moment["kind"] == "camera_transition" for moment in timeline["moments"])
    assert any(moment["kind"] == "zone_transition" for moment in timeline["moments"])
    assert any(moment["kind"] == "alert_backed" for moment in timeline["moments"])
    assert timeline["moments"][-1]["kind"] == "latest_seen"
    assert payload["storyboard"]
    assert len(payload["storyboard"]) < len(timeline["moments"])
    assert set(storyboard_ids).issubset(set(timeline_ids))
    assert storyboard_ids != timeline_ids
