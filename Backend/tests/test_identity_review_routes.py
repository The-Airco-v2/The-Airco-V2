from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid


def test_merge_unknown_persons_route_uses_service_and_returns_cluster_payload(api_client, monkeypatch):
    source_person_id = uuid.uuid4()
    target_person_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        merge_unknown_persons=AsyncMock(
            return_value=SimpleNamespace(
                cluster=SimpleNamespace(id=cluster_id, employee_id=None, cluster_state="anonymous", display_label="Merged Anonymous Identity"),
                review=SimpleNamespace(id=uuid.uuid4(), review_type="unknown_merge", decision="confirmed"),
                persons=[SimpleNamespace(id=source_person_id), SimpleNamespace(id=target_person_id)],
            )
        )
    )

    monkeypatch.setattr("api.routes.identity_reviews.IdentityReviewService", lambda db: fake_service)

    response = api_client.post(
        "/api/v2/identity-reviews/merge",
        json={
            "source_person_id": str(source_person_id),
            "target_person_ids": [str(target_person_id)],
            "reason": "same hoodie",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cluster"]["id"] == str(cluster_id)
    assert payload["review"]["type"] == "unknown_merge"
    assert payload["review"]["decision"] == "confirmed"
    assert payload["merged_person_ids"] == [str(source_person_id), str(target_person_id)]


def test_assign_employee_route_uses_service_and_returns_updated_identity(api_client, monkeypatch):
    source_person_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        assign_person_to_employee=AsyncMock(
            return_value=SimpleNamespace(
                cluster=SimpleNamespace(id=cluster_id, employee_id=employee_id, cluster_state="employee_linked", display_label="Employee Identity Cluster"),
                review=SimpleNamespace(id=uuid.uuid4(), review_type="assign_employee", decision="confirmed"),
                persons=[SimpleNamespace(id=source_person_id)],
            )
        )
    )

    monkeypatch.setattr("api.routes.identity_reviews.IdentityReviewService", lambda db: fake_service)

    response = api_client.post(
        "/api/v2/identity-reviews/assign-employee",
        json={
            "source_person_id": str(source_person_id),
            "employee_id": str(employee_id),
            "reason": "manager confirmed identity",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cluster"]["employee_id"] == str(employee_id)
    assert payload["review"]["type"] == "assign_employee"
    assert payload["merged_person_ids"] == [str(source_person_id)]


def test_undo_identity_review_route_uses_service_and_returns_reverted_status(api_client, monkeypatch):
    review_id = uuid.uuid4()
    person_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        undo_identity_review=AsyncMock(
            return_value=SimpleNamespace(
                review=SimpleNamespace(id=review_id, review_type="assign_employee", decision="reverted"),
                person=SimpleNamespace(id=person_id, employee_id=None, recognition_state="unknown", identity_cluster_id=None),
            )
        )
    )

    monkeypatch.setattr("api.routes.identity_reviews.IdentityReviewService", lambda db: fake_service)

    response = api_client.post(
        f"/api/v2/identity-reviews/{review_id}/undo",
        json={"reason": "bad merge"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["id"] == str(review_id)
    assert payload["review"]["decision"] == "reverted"
    assert payload["person"]["id"] == str(person_id)


def test_identity_review_queue_route_returns_scope_filtered_items(api_client, monkeypatch):
    session_id = uuid.uuid4()
    monkeypatch.setattr(
        "api.routes.identity_reviews.get_presigned_url",
        lambda object_name: f"http://localhost:9000/airco-evidence/{object_name}?signed=1",
    )
    fake_service = SimpleNamespace(
        list_review_queue=AsyncMock(
            return_value=[
                {
                    "review_item_id": "active:person-1",
                    "scope": "active_session",
                    "kind": "merge_suggestion",
                    "status": "open",
                    "session_id": str(session_id),
                    "session_name": "Morning Session",
                    "source_person_id": "person-1",
                    "source_cluster_id": None,
                    "representative_thumbnail_url": "snapshots/session/person-1.jpg",
                    "display_name": "Unknown Person A",
                    "confidence": 0.84,
                    "candidate_count": 1,
                    "reason_tags": ["same_session_unknown"],
                    "created_at": "2026-04-12T04:30:00Z",
                    "last_seen_at": "2026-04-12T04:45:00Z",
                }
            ]
        )
    )

    monkeypatch.setattr("api.routes.identity_reviews.IdentityReviewService", lambda db: fake_service)

    response = api_client.get(
        "/api/v2/identity-reviews/queue",
        params={"scope": "active_session", "session_id": str(session_id)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "active_session"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["review_item_id"] == "active:person-1"
    assert payload["items"][0]["representative_thumbnail_url"] == (
        "http://localhost:9000/airco-evidence/snapshots/session/person-1.jpg?signed=1"
    )


def test_identity_review_item_route_returns_detail_payload(api_client, monkeypatch):
    monkeypatch.setattr(
        "api.routes.identity_reviews.get_presigned_url",
        lambda object_name: f"http://localhost:9000/airco-evidence/{object_name}?signed=1",
    )
    fake_service = SimpleNamespace(
        get_review_item=AsyncMock(
            return_value={
                "review_item_id": "active:person-1",
                "scope": "active_session",
                "source": {
                    "person_id": "person-1",
                    "display_name": "Unknown Person A",
                    "best_thumbnail_url": "snapshots/session/person-1.jpg",
                },
                "candidates": [
                    {
                        "person_id": "person-2",
                        "display_name": "Unknown Person B",
                        "best_thumbnail_url": "snapshots/session/person-2.jpg",
                        "confidence": 0.81,
                    }
                ],
                "history": [],
            }
        )
    )

    monkeypatch.setattr("api.routes.identity_reviews.IdentityReviewService", lambda db: fake_service)

    response = api_client.get("/api/v2/identity-reviews/items/active:person-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_item_id"] == "active:person-1"
    assert payload["source"]["person_id"] == "person-1"
    assert payload["candidates"][0]["person_id"] == "person-2"
    assert payload["source"]["best_thumbnail_url"] == (
        "http://localhost:9000/airco-evidence/snapshots/session/person-1.jpg?signed=1"
    )
    assert payload["candidates"][0]["best_thumbnail_url"] == (
        "http://localhost:9000/airco-evidence/snapshots/session/person-2.jpg?signed=1"
    )


def test_identity_review_history_route_returns_review_audit_rows(api_client, monkeypatch):
    fake_service = SimpleNamespace(
        list_review_history=AsyncMock(
            return_value=[
                {
                    "review_id": str(uuid.uuid4()),
                    "review_type": "unknown_merge",
                    "decision": "confirmed",
                    "created_at": datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            ]
        )
    )

    monkeypatch.setattr("api.routes.identity_reviews.IdentityReviewService", lambda db: fake_service)

    response = api_client.get("/api/v2/identity-reviews/history")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["review_type"] == "unknown_merge"
