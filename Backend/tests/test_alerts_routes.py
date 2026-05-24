"""Alert route contract tests for Frontend alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from api_fakes import scalar_all_result, scalar_one_result
from api.routes.alerts import _acknowledged_alert_response


def test_list_alerts_returns_canonical_payload_shape(api_client, db_session_mock):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    created_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_all_result(
            [
                SimpleNamespace(
                    id=alert_id,
                    session_id=session_id,
                    session_person_id=None,
                    alert_type="phone_violation",
                    severity="high",
                    camera_id=camera_id,
                    message="Phone detected on floor",
                    status="active",
                    acknowledged_at=None,
                    created_at=created_at,
                    evidence_url=None,
                )
            ]
        ),
        scalar_one_result("Front Door"),
    ]

    response = api_client.get("/api/v2/alerts", params={"session_id": str(session_id)})

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(alert_id),
            "type": "phone_violation",
            "severity": "high",
            "camera_id": str(camera_id),
            "camera_name": "Front Door",
            "session_id": str(session_id),
            "message": "Phone detected on floor",
            "acknowledged": False,
            "created_at": "2026-03-29T13:00:00Z",
            "evidence_url": None,
            "snapshot_url": None,
        }
    ]


def test_list_alerts_supports_limit_query_param(api_client, db_session_mock):
    session_id = uuid.uuid4()
    first_alert_id = uuid.uuid4()
    second_alert_id = uuid.uuid4()
    newer = datetime(2026, 3, 29, 13, 1, tzinfo=timezone.utc)
    older = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)

    db_session_mock.execute.side_effect = [
        scalar_all_result(
            [
                SimpleNamespace(
                    id=first_alert_id,
                    session_id=session_id,
                    session_person_id=None,
                    alert_type="unknown_person",
                    severity="critical",
                    camera_id=None,
                    message="Unknown person detected",
                    status="active",
                    acknowledged_at=None,
                    created_at=newer,
                    evidence_url=None,
                ),
                SimpleNamespace(
                    id=second_alert_id,
                    session_id=session_id,
                    session_person_id=None,
                    alert_type="phone_violation",
                    severity="high",
                    camera_id=None,
                    message="Phone detected",
                    status="active",
                    acknowledged_at=None,
                    created_at=older,
                    evidence_url=None,
                ),
            ]
        )
    ]

    response = api_client.get(
        "/api/v2/alerts",
        params={"session_id": str(session_id), "limit": 1},
    )

    assert response.status_code == 200
    statement = db_session_mock.execute.await_args.args[0]
    assert " LIMIT " in str(statement)


async def test_acknowledged_alert_helper_returns_canonical_payload_shape(db_session_mock):
    alert_id = uuid.uuid4()
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    acknowledged_at = datetime(2026, 3, 29, 13, 5, tzinfo=timezone.utc)
    created_at = datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)

    db_session_mock.execute.return_value = scalar_one_result("Front Door")

    payload = await _acknowledged_alert_response(
        db_session_mock,
        SimpleNamespace(
            id=alert_id,
            alert_type="phone_violation",
            severity="high",
            camera_id=camera_id,
            session_id=session_id,
            message="Phone detected on floor",
            acknowledged_at=acknowledged_at,
            created_at=created_at,
            session_person_id=None,
            evidence_url=None,
        ),
    )

    assert payload.model_dump(mode="json") == {
        "id": str(alert_id),
        "type": "phone_violation",
        "severity": "high",
        "camera_id": str(camera_id),
        "camera_name": "Front Door",
        "session_id": str(session_id),
        "message": "Phone detected on floor",
        "acknowledged": True,
        "created_at": "2026-03-29T13:00:00Z",
        "evidence_url": None,
        "snapshot_url": None,
    }


def test_acknowledge_alert_returns_404_for_missing_alert(api_client, db_session_mock):
    missing_alert_id = uuid.uuid4()
    db_session_mock.execute.return_value = scalar_one_result(None)

    response = api_client.post(f"/api/v2/alerts/{missing_alert_id}/acknowledge")

    assert response.status_code == 404
    assert response.json()["detail"] == "Alert not found"
    assert db_session_mock.commit.await_count == 0


def test_acknowledge_alert_noops_for_already_acknowledged_alert(api_client, db_session_mock):
    alert_id = uuid.uuid4()
    db_session_mock.execute.return_value = scalar_one_result(
        SimpleNamespace(
            id=alert_id,
            alert_type="phone_violation",
            severity="high",
            camera_id=None,
            session_id=uuid.uuid4(),
            message="Phone detected on floor",
            status="acknowledged",
            acknowledged_at=datetime(2026, 3, 29, 13, 5, tzinfo=timezone.utc),
            created_at=datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc),
        )
    )

    response = api_client.post(f"/api/v2/alerts/{alert_id}/acknowledge")

    assert response.status_code == 200
    assert response.json() == {"status": "acknowledged"}
    assert db_session_mock.commit.await_count == 0
