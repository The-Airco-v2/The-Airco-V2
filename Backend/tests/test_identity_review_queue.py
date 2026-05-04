from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api.auth import AuthContext
from api_fakes import scalar_all_result, scalar_one_result
from airco.models import IdentityCluster, Session, SessionPerson
from api.identity_review_service import IdentityReviewService


def _unknown_person(*, session_id: uuid.UUID, tenant_id: str = "tenant-1", display_name: str = "Unknown Person A", cluster_id: uuid.UUID | None = None):
    now = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return SessionPerson(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        recognition_state="unknown",
        employee_id=None,
        display_name=display_name,
        identity_cluster_id=cluster_id,
        current_cameras=["cam-1"],
        active_track_bindings=[],
        first_seen_at=now,
        last_seen_at=now,
        is_active=True,
        evidence_summary={},
        face_confidence=0.41,
        body_confidence=0.84,
    )


@pytest.mark.asyncio
async def test_list_review_queue_returns_active_session_merge_suggestions():
    session_id = uuid.uuid4()
    session = Session(id=session_id, tenant_id="tenant-1", name="Morning Session")
    source_person = _unknown_person(session_id=session_id, display_name="Unknown Person A")
    candidate_person = _unknown_person(session_id=session_id, display_name="Unknown Person B")

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(session),
                scalar_all_result([source_person, candidate_person]),
            ]
        )
    )
    auth = AuthContext(user_id="ops-1", role="admin", tenant_id="tenant-1")
    service = IdentityReviewService(db)

    items = await service.list_review_queue(
        auth=auth,
        scope="active_session",
        session_id=session_id,
    )

    assert len(items) == 2
    assert all(item["scope"] == "active_session" for item in items)
    assert all(item["kind"] == "merge_suggestion" for item in items)
    assert all(item["session_id"] == str(session_id) for item in items)
    assert items[0]["candidate_count"] == 1


@pytest.mark.asyncio
async def test_list_review_queue_returns_cross_session_cluster_suggestions():
    source_session_id = uuid.uuid4()
    source_person = _unknown_person(session_id=source_session_id, display_name="Unknown Person C")
    cluster = IdentityCluster(
        id=uuid.uuid4(),
        tenant_id="tenant-1",
        employee_id=None,
        cluster_state="anonymous",
        display_label="Anonymous Cluster 01",
        body_template=[0.4, 0.5, 0.6],
    )

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                scalar_all_result([source_person]),
                scalar_all_result([cluster]),
            ]
        )
    )
    auth = AuthContext(user_id="ops-1", role="admin", tenant_id="tenant-1")
    service = IdentityReviewService(db)

    items = await service.list_review_queue(
        auth=auth,
        scope="cross_session",
        session_id=None,
    )

    assert len(items) == 1
    assert items[0]["scope"] == "cross_session"
    assert items[0]["kind"] == "cluster_candidate"
    assert items[0]["source_person_id"] == str(source_person.id)
    assert items[0]["source_cluster_id"] == str(cluster.id)
