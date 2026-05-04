from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

from api_fakes import scalar_all_result, scalar_one_result


OFFICE_TZ = ZoneInfo("Asia/Kolkata")


def _freeze_reports_now(api_client, monkeypatch, frozen_now: datetime):
    route = next(
        (candidate for candidate in api_client.app.routes if getattr(candidate, "path", None) == "/api/v2/reports/today-summary"),
        None,
    )
    if route is None:
        raise RuntimeError("reports today-summary route not loaded")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_now
            return frozen_now.astimezone(tz)

    monkeypatch.setitem(route.endpoint.__globals__, "datetime", FrozenDateTime)


def _patch_today_session(api_client, monkeypatch, session):
    route = next(
        (candidate for candidate in api_client.app.routes if getattr(candidate, "path", None) == "/api/v2/reports/today-summary"),
        None,
    )
    if route is None:
        raise RuntimeError("reports today-summary route not loaded")

    async def fake_today_session(auth, db):
        return session

    monkeypatch.setitem(route.endpoint.__globals__, "_today_session", fake_today_session)
    reports_module = route.endpoint.__globals__
    reports_module["today_attendance_log"].__globals__["_today_session"] = fake_today_session


def _patch_reports_async_helper(api_client, monkeypatch, path: str, helper_name: str, value):
    route = next(
        (candidate for candidate in api_client.app.routes if getattr(candidate, "path", None) == path),
        None,
    )
    if route is None:
        raise RuntimeError(f"reports route {path} not loaded")

    async def fake_helper(*args, **kwargs):
        return value

    monkeypatch.setitem(route.endpoint.__globals__, helper_name, fake_helper)


def test_today_summary_returns_dashboard_report_cards(api_client, db_session_mock, monkeypatch):
    frozen_now = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    _freeze_reports_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()

    session = SimpleNamespace(
        id=session_id,
        tenant_id="default",
        name="Morning Shift",
        status="running",
        created_at=frozen_now - timedelta(hours=3),
        started_at=frozen_now - timedelta(hours=3),
        stopped_at=None,
    )
    _patch_today_session(api_client, monkeypatch, session)

    db_session_mock.execute.side_effect = [
        scalar_all_result(
            [
                SimpleNamespace(id=uuid.uuid4(), tenant_id="default", is_active=True, is_entrance=True),
                SimpleNamespace(id=uuid.uuid4(), tenant_id="default", is_active=True, is_entrance=False),
                SimpleNamespace(id=uuid.uuid4(), tenant_id="default", is_active=False, is_entrance=False),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(id=alice_id, tenant_id="default", name="Alice", status="active"),
                SimpleNamespace(id=bob_id, tenant_id="default", name="Bob", status="active"),
            ]
        ),
        scalar_all_result([SimpleNamespace(session_id=session_id, event_type="check_in", employee_id=alice_id, time=frozen_now - timedelta(hours=2))]),
        scalar_one_result(79),
        scalar_one_result(1),
    ]

    response = api_client.get("/api/v2/reports/today-summary")

    assert response.status_code == 200
    assert response.json() == {
        "session": {
            "id": str(session_id),
            "name": "Morning Shift",
            "status": "running",
        },
        "cards": {
            "present_today": 1,
            "absent_today": 1,
            "late_arrivals": 0,
            "avg_productivity": 79,
            "active_cameras": {"online": 2, "total": 3},
            "footfall_today": 1,
        },
    }


def test_today_attendance_log_returns_report_table_rows(api_client, db_session_mock, monkeypatch):
    frozen_now = datetime(2026, 4, 4, 12, 0, tzinfo=OFFICE_TZ)
    _freeze_reports_now(api_client, monkeypatch, frozen_now)

    session_id = uuid.uuid4()
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()

    session = SimpleNamespace(
        id=session_id,
        tenant_id="default",
        name="Morning Shift",
        status="running",
        created_at=frozen_now - timedelta(hours=3),
        started_at=frozen_now - timedelta(hours=3),
        stopped_at=None,
    )
    _patch_today_session(api_client, monkeypatch, session)

    db_session_mock.execute.side_effect = [
        scalar_all_result(
            [
                SimpleNamespace(id=alice_id, tenant_id="default", name="Alice", department="Ops", status="active"),
                SimpleNamespace(id=bob_id, tenant_id="default", name="Bob", department="HR", status="active"),
            ]
        ),
        scalar_all_result(
            [
                SimpleNamespace(
                    employee_id=alice_id,
                    time=frozen_now - timedelta(hours=2, minutes=15),
                    event_type="check_in",
                ),
                SimpleNamespace(
                    employee_id=bob_id,
                    time=frozen_now - timedelta(hours=1, minutes=30),
                    event_type="check_in",
                ),
            ]
        ),
        scalar_one_result(
            SimpleNamespace(
                session_person_id=uuid.uuid4(),
                employee_id=alice_id,
                first_seen_at=frozen_now - timedelta(hours=2, minutes=15),
                last_seen_at=frozen_now - timedelta(minutes=10),
            )
        ),
        scalar_one_result(82),
        scalar_one_result(1),
        scalar_one_result(
            SimpleNamespace(
                session_person_id=uuid.uuid4(),
                employee_id=bob_id,
                first_seen_at=frozen_now - timedelta(hours=1, minutes=30),
                last_seen_at=frozen_now - timedelta(minutes=20),
            )
        ),
        scalar_one_result(63),
        scalar_one_result(0),
    ]

    response = api_client.get("/api/v2/reports/today-attendance-log")

    assert response.status_code == 200
    assert response.json() == {
        "session": {
            "id": str(session_id),
            "name": "Morning Shift",
            "status": "running",
        },
        "rows": [
                {
                    "employee_id": str(alice_id),
                    "employee_name": "Alice",
                    "department": "Ops",
                    "first_entry": "2026-04-04T04:15:00Z",
                    "last_seen": "2026-04-04T06:20:00Z",
                    "total_work_minutes": 125,
                    "status": "on_time",
                    "violations": 1,
                    "productivity_percent": 82,
                },
            {
                    "employee_id": str(bob_id),
                    "employee_name": "Bob",
                    "department": "HR",
                    "first_entry": "2026-04-04T05:00:00Z",
                    "last_seen": "2026-04-04T06:10:00Z",
                    "total_work_minutes": 70,
                    "status": "late",
                    "violations": 0,
                    "productivity_percent": 63,
            },
        ],
    }


def test_today_insights_returns_report_cards(api_client, monkeypatch):
    session_id = uuid.uuid4()
    payload = {
        "session": {
            "id": str(session_id),
            "name": "Morning Shift",
            "status": "running",
        },
        "cards": {
            "late_arrivals": [
                {
                    "employee_id": str(uuid.uuid4()),
                    "employee_name": "Nina Patel",
                    "department": "Engineering",
                    "first_entry": "2026-04-04T03:45:00Z",
                }
            ],
            "on_time": [
                {
                    "employee_id": str(uuid.uuid4()),
                    "employee_name": "James Liu",
                    "department": "Finance",
                    "first_entry": "2026-04-04T03:05:00Z",
                }
            ],
            "phone_usage": [
                {
                    "employee_id": str(uuid.uuid4()),
                    "employee_name": "Nina Patel",
                    "department": "Engineering",
                    "phone_usage_minutes": 7.5,
                }
            ],
            "violations": [
                {
                    "employee_id": str(uuid.uuid4()),
                    "employee_name": "Priya Sharma",
                    "department": "HR",
                    "violations": 2,
                }
            ],
        },
    }
    _patch_reports_async_helper(
        api_client,
        monkeypatch,
        "/api/v2/reports/today-insights",
        "_today_insights_payload",
        payload,
    )

    response = api_client.get("/api/v2/reports/today-insights")

    assert response.status_code == 200
    assert response.json() == payload


def test_day_summary_returns_selected_historical_snapshot(api_client, monkeypatch):
    payload = {
        "date": "2026-04-01",
        "label": "Apr 1, 2026",
        "summary": {
            "present": 7,
            "absent": 1,
            "late_arrivals": 2,
            "avg_productivity": 75,
            "active_cameras": 8,
            "footfall": 42,
            "violations": 4,
        },
        "employees": [
            {
                "employee_id": str(uuid.uuid4()),
                "employee_name": "Alex Carter",
                "department": "Engineering",
                "first_entry": "2026-04-01T03:00:00Z",
                "last_seen": "2026-04-01T08:45:00Z",
                "total_work_minutes": 345,
                "status": "on_time",
                "violations": 0,
                "productivity_percent": 89,
            }
        ],
    }
    _patch_reports_async_helper(
        api_client,
        monkeypatch,
        "/api/v2/reports/day-summary",
        "_day_summary_payload",
        payload,
    )

    response = api_client.get("/api/v2/reports/day-summary", params={"date": "2026-04-01"})

    assert response.status_code == 200
    assert response.json() == payload


def test_monthly_timeline_returns_daywise_eod_summaries(api_client, monkeypatch):
    payload = {
        "days": [
            {
                "date": "2026-04-01",
                "label": "Apr 1, 2026",
                "summary": {
                    "present": 7,
                    "absent": 1,
                    "late_arrivals": 2,
                    "avg_productivity": 75,
                    "active_cameras": 8,
                    "footfall": 42,
                    "violations": 4,
                },
            },
            {
                "date": "2026-03-31",
                "label": "Mar 31, 2026",
                "summary": {
                    "present": 6,
                    "absent": 2,
                    "late_arrivals": 1,
                    "avg_productivity": 81,
                    "active_cameras": 9,
                    "footfall": 38,
                    "violations": 2,
                },
            },
        ]
    }
    _patch_reports_async_helper(
        api_client,
        monkeypatch,
        "/api/v2/reports/monthly-timeline",
        "_monthly_timeline_payload",
        payload,
    )

    response = api_client.get("/api/v2/reports/monthly-timeline", params={"days": 30})

    assert response.status_code == 200
    assert response.json() == payload


def test_employee_analysis_returns_attendance_and_performance_rollups(api_client, monkeypatch):
    payload = {
        "days": 30,
        "employees": [
            {
                "employee_id": str(uuid.uuid4()),
                "employee_name": "James Liu",
                "department": "Finance",
                "avg_productivity_percent": 93,
                "late_count": 0,
                "avg_work_hours": 6.4,
                "days_present": 20,
                "days_absent": 0,
                "violations": 0,
            },
            {
                "employee_id": str(uuid.uuid4()),
                "employee_name": "Nina Patel",
                "department": "Engineering",
                "avg_productivity_percent": 50,
                "late_count": 3,
                "avg_work_hours": 3.8,
                "days_present": 18,
                "days_absent": 2,
                "violations": 4,
            },
        ],
    }
    _patch_reports_async_helper(
        api_client,
        monkeypatch,
        "/api/v2/reports/employee-analysis",
        "_employee_analysis_payload",
        payload,
    )

    response = api_client.get("/api/v2/reports/employee-analysis", params={"days": 30})

    assert response.status_code == 200
    assert response.json() == payload


def test_leaderboards_returns_ranked_report_lists(api_client, monkeypatch):
    payload = {
        "days": 30,
        "performers": [
            {"rank": 1, "employee_name": "James Liu", "score": 95},
            {"rank": 2, "employee_name": "Alex Carter", "score": 91},
        ],
        "attendance": [
            {"rank": 1, "employee_name": "Mohammed Al-Rashid", "attendance_percent": 100},
            {"rank": 2, "employee_name": "James Liu", "attendance_percent": 100},
        ],
        "late_behavior": [
            {"rank": 1, "employee_name": "Nina Patel", "late_count": 3},
            {"rank": 2, "employee_name": "Priya Sharma", "late_count": 1},
        ],
        "low_work_hours": [
            {"rank": 1, "employee_name": "Sara Okonkwo", "avg_work_hours": 4.2},
            {"rank": 2, "employee_name": "Nina Patel", "avg_work_hours": 4.5},
        ],
    }
    _patch_reports_async_helper(
        api_client,
        monkeypatch,
        "/api/v2/reports/leaderboards",
        "_leaderboards_payload",
        payload,
    )

    response = api_client.get("/api/v2/reports/leaderboards", params={"days": 30})

    assert response.status_code == 200
    assert response.json() == payload
