from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from api_fakes import rows_result, scalar_all_result, scalar_one_result


def _patch_exception_queue(api_client, monkeypatch, fake_list_exceptions):
    route = next(
        (candidate for candidate in api_client.app.routes if getattr(candidate, "path", None) == "/api/v2/reports/trends"),
        None,
    )
    if route is None:
        raise RuntimeError("analytics trends route not loaded")
    exceptions_module = route.endpoint.__globals__["exceptions_route"]
    monkeypatch.setattr(exceptions_module, "list_exceptions", fake_list_exceptions)


def test_trends_payload_uses_exception_queue_semantics_for_open_counts(
    api_client, db_session_mock, monkeypatch
):
    session_id = uuid.uuid4()
    session_person_id = uuid.uuid4()
    session_created_at = datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc)
    session = SimpleNamespace(
        id=session_id,
        tenant_id="default",
        name="Morning Shift",
        status="completed",
        created_at=session_created_at,
    )

    async def fake_list_exceptions(*, session_id, auth, db, **_kwargs):
        assert str(auth.tenant_id) == "default"
        assert session_id == session.id
        return [
            {"id": "exc-1", "status": "active", "created_at": "2026-03-22T10:00:00+00:00"},
            {"id": "exc-2", "status": "resolved", "created_at": "2026-03-22T09:00:00+00:00"},
        ]

    _patch_exception_queue(api_client, monkeypatch, fake_list_exceptions)

    db_session_mock.execute.side_effect = [
        scalar_all_result([session]),
        scalar_one_result(1),
        scalar_one_result(2),
        scalar_one_result(1),
        rows_result([(session_person_id,)]),
        rows_result([("working", 3), ("idle", 1)]),
        scalar_one_result(0.0),
    ]

    response = api_client.get("/api/v2/reports/trends", params={"days": 7})

    assert response.status_code == 200
    assert response.json()["trends"][0]["total_open_exceptions"] == 1
    assert response.json() == {
        "period_days": 7,
        "trends": [
            {
                "date": session_created_at.date().isoformat(),
                "sessions": 1,
                "total_persons": 1,
                "total_alerts": 2,
                "total_check_ins": 1,
                "avg_productivity": 75,
                "total_phone_minutes": 0.0,
                "total_open_exceptions": 1,
            }
        ],
    }


def test_trends_payload_rolls_same_day_sessions_and_counts_stable_exception_pressure(
    api_client, db_session_mock, monkeypatch
):
    session_a_id = uuid.uuid4()
    session_b_id = uuid.uuid4()
    session_a_person_id = uuid.uuid4()
    session_b_person_id = uuid.uuid4()
    session_created_at = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)
    session_a = SimpleNamespace(
        id=session_a_id,
        tenant_id="default",
        name="Morning Shift",
        status="completed",
        created_at=session_created_at,
    )
    session_b = SimpleNamespace(
        id=session_b_id,
        tenant_id="default",
        name="Afternoon Shift",
        status="completed",
        created_at=session_created_at + timedelta(hours=2),
    )

    async def fake_list_exceptions(*, session_id, auth, db, **_kwargs):
        assert str(auth.tenant_id) == "default"
        if session_id == session_a_id:
            return [{"id": "exc-a", "status": "active", "created_at": "2026-03-22T09:30:00+00:00"}]
        if session_id == session_b_id:
            return []
        raise AssertionError(f"unexpected session_id: {session_id}")

    _patch_exception_queue(api_client, monkeypatch, fake_list_exceptions)

    db_session_mock.execute.side_effect = [
        scalar_all_result([session_a, session_b]),
        scalar_one_result(1),
        scalar_one_result(1),
        scalar_one_result(1),
        rows_result([(session_a_person_id,)]),
        rows_result([("working", 3), ("idle", 1)]),
        scalar_one_result(120.0),
        scalar_one_result(1),
        scalar_one_result(1),
        scalar_one_result(1),
        rows_result([(session_b_person_id,)]),
        rows_result([]),
        scalar_one_result(None),
    ]

    response = api_client.get("/api/v2/reports/trends", params={"days": 7})

    assert response.status_code == 200
    assert response.json()["trends"][0]["total_open_exceptions"] == 1
    assert response.json() == {
        "period_days": 7,
        "trends": [
            {
                "date": session_created_at.date().isoformat(),
                "sessions": 2,
                "total_persons": 2,
                "total_alerts": 2,
                "total_check_ins": 2,
                "avg_productivity": 75,
                "total_phone_minutes": 2.0,
                "total_open_exceptions": 1,
            }
        ],
    }


def test_trends_bucket_sessions_by_exception_queue_day_when_it_differs_from_created_at(
    api_client, db_session_mock, monkeypatch
):
    session_id = uuid.uuid4()
    session_person_id = uuid.uuid4()
    session_created_at = datetime(2026, 3, 21, 18, 30, tzinfo=timezone.utc)
    session = SimpleNamespace(
        id=session_id,
        tenant_id="default",
        name="Overnight Shift",
        status="completed",
        created_at=session_created_at,
    )

    async def fake_list_exceptions(*, session_id, auth, db, **_kwargs):
        assert str(auth.tenant_id) == "default"
        assert session_id == session.id
        return [
            {"id": "exc-overnight", "status": "active", "created_at": "2026-03-21T19:00:00+00:00"},
        ]

    _patch_exception_queue(api_client, monkeypatch, fake_list_exceptions)

    db_session_mock.execute.side_effect = [
        scalar_all_result([session]),
        scalar_one_result(1),
        scalar_one_result(0),
        scalar_one_result(1),
        rows_result([(session_person_id,)]),
        rows_result([("working", 2), ("idle", 2)]),
        scalar_one_result(None),
    ]

    response = api_client.get("/api/v2/reports/trends", params={"days": 7})

    assert response.status_code == 200
    assert response.json() == {
        "period_days": 7,
        "trends": [
            {
                "date": "2026-03-22",
                "sessions": 1,
                "total_persons": 1,
                "total_alerts": 0,
                "total_check_ins": 1,
                "avg_productivity": 50,
                "total_phone_minutes": 0.0,
                "total_open_exceptions": 1,
            }
        ],
    }
