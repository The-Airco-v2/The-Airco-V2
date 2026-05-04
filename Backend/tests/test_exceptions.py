"""Contract test for the exceptions aggregation route."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import uuid

from api_fakes import scalar_all_result, scalar_one_result


OFFICE_TZ = ZoneInfo("Asia/Kolkata")


def _freeze_exceptions_now(api_client, monkeypatch, frozen_now: datetime):
    route = next(
        (candidate for candidate in api_client.app.routes if getattr(candidate, "path", None) == "/api/v2/exceptions"),
        None,
    )
    if route is None:
        raise RuntimeError("exceptions route not loaded")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_now
            return frozen_now.astimezone(tz)

    monkeypatch.setitem(route.endpoint.__globals__, "datetime", FrozenDateTime)


def test_exceptions_route_returns_normalized_queue_across_sources(api_client, db_session_mock):
    session_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    alert_person_id = uuid.uuid4()
    identity_person_id = uuid.uuid4()
    lobby_camera_id = uuid.uuid4()
    restricted_camera_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    review_id = uuid.uuid4()

    late_checkin_time = datetime(2026, 3, 21, 10, 5, tzinfo=OFFICE_TZ)
    identity_seen_time = datetime(2026, 3, 21, 10, 14, tzinfo=OFFICE_TZ)
    review_time = datetime(2026, 3, 21, 10, 16, tzinfo=OFFICE_TZ)
    phone_time = datetime(2026, 3, 21, 10, 18, tzinfo=OFFICE_TZ)
    alert_time = datetime(2026, 3, 21, 10, 20, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                started_at=datetime(2026, 3, 21, 8, 0, tzinfo=OFFICE_TZ),
                created_at=datetime(2026, 3, 21, 7, 45, tzinfo=OFFICE_TZ),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=employee_id,
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
                    id=lobby_camera_id,
                    tenant_id="default",
                    name="Lobby Cam",
                    location="Lobby",
                    zone="Lobby",
                    rtsp_url="rtsp://lobby",
                    is_entrance=True,
                    is_active=True,
                ),
                SimpleNamespace(
                    id=restricted_camera_id,
                    tenant_id="default",
                    name="Vault Cam",
                    location="Vault",
                    zone="Restricted Lab",
                    rtsp_url="rtsp://vault",
                    is_entrance=False,
                    is_active=True,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alert_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="identified",
                    employee_id=employee_id,
                    display_name="Alice",
                    current_cameras=[str(lobby_camera_id)],
                    active_track_bindings={},
                    face_confidence=0.98,
                    body_confidence=0.87,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=late_checkin_time,
                    last_seen_at=phone_time,
                    is_active=True,
                    evidence_summary={},
                ),
                SimpleNamespace(
                    id=identity_person_id,
                    session_id=session_id,
                    tenant_id="default",
                    recognition_state="candidate",
                    employee_id=None,
                    display_name="Unknown Visitor",
                    current_cameras=[str(restricted_camera_id)],
                    active_track_bindings={},
                    face_confidence=0.42,
                    body_confidence=0.51,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=identity_seen_time,
                    last_seen_at=identity_seen_time,
                    is_active=True,
                    evidence_summary={"top_match": "E099"},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alert_id,
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=alert_person_id,
                    alert_type="restricted_zone",
                    severity="high",
                    camera_id=restricted_camera_id,
                    evidence_url=None,
                    message="Alice entered Restricted Lab",
                    status="active",
                    dedup_key="restricted-zone-alice",
                    created_at=alert_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=review_id,
                    session_id=session_id,
                    session_person_id=identity_person_id,
                    task_type="unknown_review",
                    status="pending",
                    evidence={"reason": "candidate match below threshold"},
                    decision=None,
                    created_at=review_time,
                    resolved_at=None,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=phone_time,
                    session_id=session_id,
                    session_person_id=alert_person_id,
                    camera_id=lobby_camera_id,
                    track_id=101,
                    confidence=0.91,
                    duration_seconds=45.0,
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=late_checkin_time,
                    session_id=session_id,
                    tenant_id="default",
                    employee_id=employee_id,
                    session_person_id=alert_person_id,
                    camera_id=lobby_camera_id,
                    event_type="check_in",
                    confidence=0.97,
                ),
            ]
        ),
    ]

    response = api_client.get("/api/v2/exceptions", params={"session_id": str(session_id)})

    assert response.status_code == 200
    payload = response.json()
    assert [item["category"] for item in payload] == [
        "restricted_zone",
        "phone_violation",
        "review_pending",
        "identity_low_confidence",
        "late_arrival",
    ]

    required_keys = {
        "id",
        "source",
        "category",
        "severity",
        "title",
        "subtitle",
        "employee_id",
        "employee_name",
        "confidence",
        "camera",
        "zone",
        "created_at",
        "status",
        "recommended_action",
        "audit_context",
    }
    assert all(required_keys.issubset(item) for item in payload)

    restricted_zone_item = payload[0]
    assert restricted_zone_item["id"] == str(alert_id)
    assert restricted_zone_item["source"] == "alert"
    assert restricted_zone_item["employee_id"] == str(employee_id)
    assert restricted_zone_item["employee_name"] == "Alice"
    assert restricted_zone_item["camera"] == "Vault Cam"
    assert restricted_zone_item["zone"] == "Restricted Lab"
    assert restricted_zone_item["status"] == "active"

    phone_item = payload[1]
    assert phone_item["source"] == "behavior"
    assert phone_item["employee_name"] == "Alice"
    assert phone_item["confidence"] == 0.91
    assert phone_item["camera"] == "Lobby Cam"

    review_item = payload[2]
    assert review_item["id"] == str(review_id)
    assert review_item["source"] == "review"
    assert review_item["status"] == "pending"
    assert review_item["employee_id"] is None
    assert review_item["employee_name"] == "Unknown Visitor"

    identity_item = payload[3]
    assert identity_item["id"] == str(identity_person_id)
    assert identity_item["source"] == "identity"
    assert identity_item["employee_name"] == "Unknown Visitor"
    assert identity_item["confidence"] == 0.42

    late_item = payload[4]
    assert late_item["source"] == "attendance"
    assert late_item["employee_id"] == str(employee_id)
    assert late_item["employee_name"] == "Alice"
    assert late_item["camera"] == "Lobby Cam"
    assert late_item["created_at"] == late_checkin_time.isoformat()


def test_exceptions_route_maps_alert_backed_identity_and_behavior_sources(api_client, db_session_mock):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    phone_alert_id = uuid.uuid4()
    unknown_alert_id = uuid.uuid4()

    unknown_alert_time = datetime(2026, 3, 22, 10, 10, tzinfo=OFFICE_TZ)
    phone_alert_time = datetime(2026, 3, 22, 10, 20, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                started_at=datetime(2026, 3, 22, 8, 0, tzinfo=OFFICE_TZ),
                created_at=datetime(2026, 3, 22, 7, 45, tzinfo=OFFICE_TZ),
            )
        ),
        scalar_all_result([]),
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
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=phone_alert_id,
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=None,
                    alert_type="phone_violation",
                    severity="high",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Phone detected in lobby",
                    status="active",
                    dedup_key="phone-alert",
                    created_at=phone_alert_time,
                    acknowledged_at=None,
                ),
                SimpleNamespace(
                    id=unknown_alert_id,
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=None,
                    alert_type="unknown_person",
                    severity="medium",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Unknown person detected in lobby",
                    status="active",
                    dedup_key="unknown-alert",
                    created_at=unknown_alert_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/exceptions", params={"session_id": str(session_id)})

    assert response.status_code == 200
    payload = response.json()
    items_by_category = {item["category"]: item for item in payload}

    assert items_by_category["phone_violation"]["id"] == str(phone_alert_id)
    assert items_by_category["phone_violation"]["source"] == "behavior"
    assert items_by_category["identity_unknown"]["id"] == str(unknown_alert_id)
    assert items_by_category["identity_unknown"]["source"] == "identity"


def test_exceptions_route_deduplicates_alert_backed_phone_violation(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    phone_time = datetime(2026, 3, 22, 10, 20, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                started_at=datetime(2026, 3, 22, 8, 0, tzinfo=OFFICE_TZ),
                created_at=datetime(2026, 3, 22, 7, 45, tzinfo=OFFICE_TZ),
            )
        ),
        scalar_all_result([]),
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
                    recognition_state="identified",
                    employee_id=None,
                    display_name="Alice",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.97,
                    body_confidence=0.88,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=phone_time,
                    last_seen_at=phone_time,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alert_id,
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=person_id,
                    alert_type="phone_violation",
                    severity="high",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Phone usage for 45s",
                    status="active",
                    dedup_key="phone-alert",
                    created_at=phone_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=phone_time,
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    track_id=55,
                    confidence=0.92,
                    duration_seconds=45.0,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/exceptions", params={"session_id": str(session_id)})

    assert response.status_code == 200
    payload = response.json()
    phone_items = [item for item in payload if item["category"] == "phone_violation"]
    assert len(phone_items) == 1
    assert phone_items[0]["id"] == str(alert_id)
    assert phone_items[0]["source"] == "behavior"


def test_exceptions_route_deduplicates_alert_backed_unknown_identity(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    seen_time = datetime(2026, 3, 22, 10, 10, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                started_at=datetime(2026, 3, 22, 8, 0, tzinfo=OFFICE_TZ),
                created_at=datetime(2026, 3, 22, 7, 45, tzinfo=OFFICE_TZ),
            )
        ),
        scalar_all_result([]),
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
                    display_name="Unknown Visitor",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.31,
                    body_confidence=0.48,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=seen_time,
                    last_seen_at=seen_time,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alert_id,
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=person_id,
                    alert_type="unknown_person",
                    severity="medium",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Unknown person detected on camera",
                    status="active",
                    dedup_key="unknown-alert",
                    created_at=seen_time,
                    acknowledged_at=None,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/exceptions", params={"session_id": str(session_id)})

    assert response.status_code == 200
    payload = response.json()
    identity_items = [item for item in payload if item["category"] == "identity_unknown"]
    assert len(identity_items) == 1
    assert identity_items[0]["id"] == str(alert_id)
    assert identity_items[0]["source"] == "identity"


def test_exceptions_route_uses_latest_office_day_for_overnight_session(api_client, db_session_mock):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    started_at = datetime(2026, 3, 21, 23, 30, tzinfo=OFFICE_TZ)
    next_day_phone_time = datetime(2026, 3, 22, 0, 20, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Overnight Shift",
                status="running",
                started_at=started_at,
                created_at=started_at,
            )
        ),
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="Night Cam",
                    location="Dock",
                    zone="Dock",
                    rtsp_url="rtsp://night",
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
                    recognition_state="identified",
                    employee_id=None,
                    display_name="Night Owl",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.89,
                    body_confidence=0.73,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=next_day_phone_time,
                    last_seen_at=next_day_phone_time,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=next_day_phone_time,
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    track_id=77,
                    confidence=0.9,
                    duration_seconds=45.0,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get("/api/v2/exceptions", params={"session_id": str(session_id)})

    assert response.status_code == 200
    payload = response.json()
    phone_items = [item for item in payload if item["category"] == "phone_violation"]
    assert len(phone_items) == 1
    assert phone_items[0]["created_at"] == next_day_phone_time.isoformat()
    assert phone_items[0]["employee_name"] == "Night Owl"


def test_exceptions_route_non_active_phone_alert_does_not_suppress_active_synthesized_exception(
    api_client,
    db_session_mock,
):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    resolved_alert_id = uuid.uuid4()
    phone_time = datetime(2026, 3, 22, 10, 20, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Morning Shift",
                status="running",
                started_at=datetime(2026, 3, 22, 8, 0, tzinfo=OFFICE_TZ),
                created_at=datetime(2026, 3, 22, 7, 45, tzinfo=OFFICE_TZ),
            )
        ),
        scalar_all_result([]),
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
                    recognition_state="identified",
                    employee_id=None,
                    display_name="Alice",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.97,
                    body_confidence=0.88,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=phone_time,
                    last_seen_at=phone_time,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=resolved_alert_id,
                    session_id=session_id,
                    tenant_id="default",
                    session_person_id=person_id,
                    alert_type="phone_violation",
                    severity="high",
                    camera_id=camera_id,
                    evidence_url=None,
                    message="Phone usage for 45s",
                    status="resolved",
                    dedup_key="phone-alert",
                    created_at=phone_time,
                    acknowledged_at=phone_time,
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result(
            [
                SimpleNamespace(
                    time=phone_time,
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    track_id=55,
                    confidence=0.92,
                    duration_seconds=45.0,
                ),
            ]
        ),
        scalar_all_result([]),
    ]

    response = api_client.get(
        "/api/v2/exceptions",
        params={"session_id": str(session_id), "status": "active"},
    )

    assert response.status_code == 200
    payload = response.json()
    phone_items = [item for item in payload if item["category"] == "phone_violation"]
    assert len(phone_items) == 1
    assert phone_items[0]["id"] == f"phone:{person_id}"
    assert phone_items[0]["status"] == "active"
    assert phone_items[0]["source"] == "behavior"


def test_exceptions_route_does_not_mark_overnight_carryover_presence_absent(
    api_client,
    db_session_mock,
    monkeypatch,
):
    frozen_now = datetime(2026, 3, 22, 12, 0, tzinfo=OFFICE_TZ)
    _freeze_exceptions_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    person_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    first_seen_previous_day = datetime(2026, 3, 21, 23, 50, tzinfo=OFFICE_TZ)
    last_seen_selected_day = datetime(2026, 3, 22, 11, 30, tzinfo=OFFICE_TZ)

    db_session_mock.execute.side_effect = [
        scalar_one_result(
            SimpleNamespace(
                id=session_id,
                tenant_id="default",
                name="Overnight Shift",
                status="running",
                started_at=datetime(2026, 3, 21, 23, 0, tzinfo=OFFICE_TZ),
                created_at=datetime(2026, 3, 21, 22, 45, tzinfo=OFFICE_TZ),
            )
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=employee_id,
                    tenant_id="default",
                    name="Night Owl",
                    employee_code="E001",
                    department="Ops",
                    status="active",
                ),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    id=camera_id,
                    tenant_id="default",
                    name="Night Cam",
                    location="Dock",
                    zone="Dock",
                    rtsp_url="rtsp://night",
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
                    recognition_state="identified",
                    employee_id=employee_id,
                    display_name="Night Owl",
                    current_cameras=[str(camera_id)],
                    active_track_bindings={},
                    face_confidence=0.91,
                    body_confidence=0.78,
                    identity_conflict=False,
                    best_thumbnail_url=None,
                    first_seen_at=first_seen_previous_day,
                    last_seen_at=last_seen_selected_day,
                    is_active=True,
                    evidence_summary={},
                ),
            ]
        ),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
        scalar_all_result([]),
    ]

    response = api_client.get(
        "/api/v2/exceptions",
        params={"session_id": str(session_id), "category": "absence"},
    )

    assert response.status_code == 200
    assert response.json() == []
