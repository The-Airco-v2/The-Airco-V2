"""Employee route contract tests for Frontend alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import io
import uuid

from api_fakes import scalar_all_result, scalar_one_result


def test_create_employee_returns_canonical_employee_shape(api_client, db_session_mock):
    employee_id = uuid.uuid4()
    created_at = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)

    db_session_mock.add = lambda _: None
    db_session_mock.execute.side_effect = [scalar_one_result(0)]
    db_session_mock.refresh.side_effect = lambda employee: [
        setattr(employee, "id", employee_id),
        setattr(employee, "created_at", created_at),
        setattr(employee, "status", "active"),
    ]

    response = api_client.post(
        "/api/v2/employees",
        json={"name": "Nikhil", "department": "IT"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(employee_id),
        "name": "Nikhil",
        "department": "IT",
        "enrollment_status": "untrained",
        "photo_url": None,
        "created_at": "2026-03-29T12:00:00Z",
    }


def test_list_employees_returns_canonical_employee_shape(api_client, db_session_mock):
    created_at = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    db_session_mock.execute.return_value = scalar_all_result(
        [
            SimpleNamespace(
                id=uuid.uuid4(),
                name="Nikhil",
                department="IT",
                status="active",
                created_at=created_at,
            )
        ]
    )

    response = api_client.get("/api/v2/employees")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(db_session_mock.execute.return_value.all()[0].id),
            "name": "Nikhil",
            "department": "IT",
            "enrollment_status": "untrained",
            "photo_url": None,
            "created_at": "2026-03-29T12:00:00Z",
        }
    ]


def test_delete_employee_route_exists_and_returns_no_content(api_client, db_session_mock):
    employee_id = uuid.uuid4()
    db_session_mock.execute.return_value = scalar_one_result(
        SimpleNamespace(id=employee_id, tenant_id="default", name="Nikhil")
    )

    response = api_client.delete(f"/api/v2/employees/{employee_id}")

    assert response.status_code == 204
    assert response.content == b""


def test_enroll_employee_accepts_file_field(api_client, db_session_mock):
    employee_id = uuid.uuid4()
    db_session_mock.add = lambda _: None
    db_session_mock.execute.return_value = scalar_one_result(
        SimpleNamespace(id=employee_id, tenant_id="default", name="Nikhil")
    )

    response = api_client.post(
        f"/api/v2/employees/{employee_id}/enroll",
        params={"angle": "front"},
        files={"file": ("face.jpg", io.BytesIO(b"fake-image"), "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "enrolled",
        "employee_id": str(employee_id),
        "angle": "front",
    }
