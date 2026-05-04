from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api.auth import AuthContext
from api_fakes import scalar_all_result, scalar_one_result
from airco.models import (
    IdentityCluster,
    IdentityClusterMember,
    IdentityMergeReview,
    PersonEmbedding,
    SessionPerson,
)
from api.identity_review_service import IdentityReviewService


def _unknown_person(*, person_id: uuid.UUID, tenant_id: str = "tenant-1", cluster_id: uuid.UUID | None = None):
    now = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return SessionPerson(
        id=person_id,
        session_id=uuid.uuid4(),
        tenant_id=tenant_id,
        recognition_state="unknown",
        employee_id=None,
        display_name=f"Unknown Person {person_id.hex[:8].upper()}",
        identity_cluster_id=cluster_id,
        current_cameras=[],
        active_track_bindings=[],
        first_seen_at=now,
        last_seen_at=now,
        is_active=True,
        evidence_summary={},
    )


@pytest.mark.asyncio
async def test_merge_unknown_persons_creates_cluster_and_membership():
    source_person = _unknown_person(person_id=uuid.uuid4())
    target_person = _unknown_person(person_id=uuid.uuid4())
    embeddings = [
        PersonEmbedding(
            id=uuid.uuid4(),
            session_person_id=source_person.id,
            embedding_type="body",
            embedding=[0.2, 0.2, 0.2],
            quality_score=0.9,
            camera_id=None,
        ),
        PersonEmbedding(
            id=uuid.uuid4(),
            session_person_id=target_person.id,
            embedding_type="body",
            embedding=[0.6, 0.6, 0.6],
            quality_score=0.95,
            camera_id=None,
        ),
    ]
    added: list[object] = []
    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(source_person),
                scalar_all_result([target_person]),
                scalar_all_result(embeddings),
            ]
        ),
    )
    auth = AuthContext(user_id="user-1", role="admin", tenant_id="tenant-1")

    service = IdentityReviewService(db)

    result = await service.merge_unknown_persons(
        auth=auth,
        source_person_id=source_person.id,
        target_person_ids=[target_person.id],
        reason="same jacket and timeline",
    )

    cluster = next(model for model in added if isinstance(model, IdentityCluster))
    members = [model for model in added if isinstance(model, IdentityClusterMember)]
    review = next(model for model in added if isinstance(model, IdentityMergeReview))

    assert isinstance(result.cluster, IdentityCluster)
    assert result.cluster is cluster
    assert cluster.tenant_id == "tenant-1"
    assert cluster.cluster_state == "anonymous"
    assert source_person.identity_cluster_id == cluster.id
    assert target_person.identity_cluster_id == cluster.id
    assert len(members) == 2
    assert {member.session_person_id for member in members} == {source_person.id, target_person.id}
    assert review.review_type == "unknown_merge"
    assert review.created_by == "user-1"
    assert review.reason == "same jacket and timeline"
    assert cluster.body_template == pytest.approx([0.4, 0.4, 0.4])
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_merge_unknown_persons_assigns_cluster_id_before_membership_rows():
    source_person = _unknown_person(person_id=uuid.uuid4())
    target_person = _unknown_person(person_id=uuid.uuid4())
    added: list[object] = []
    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(source_person),
                scalar_all_result([target_person]),
                scalar_all_result([]),
            ]
        ),
    )
    auth = AuthContext(user_id="user-1", role="admin", tenant_id="tenant-1")

    service = IdentityReviewService(db)

    result = await service.merge_unknown_persons(
        auth=auth,
        source_person_id=source_person.id,
        target_person_ids=[target_person.id],
    )

    assert result.cluster.id is not None
    members = [model for model in added if isinstance(model, IdentityClusterMember)]
    assert members
    assert all(member.identity_cluster_id == result.cluster.id for member in members)
    assert source_person.identity_cluster_id == result.cluster.id
    assert target_person.identity_cluster_id == result.cluster.id


@pytest.mark.asyncio
async def test_assign_person_to_employee_links_employee_cluster_and_updates_person():
    person = _unknown_person(person_id=uuid.uuid4())
    employee_id = uuid.uuid4()
    employee_cluster = IdentityCluster(
        id=uuid.uuid4(),
        tenant_id="tenant-1",
        employee_id=employee_id,
        cluster_state="employee_linked",
        display_label="Employee Cluster",
    )
    embeddings = [
        PersonEmbedding(
            id=uuid.uuid4(),
            session_person_id=person.id,
            embedding_type="face",
            embedding=[0.3, 0.3, 0.3],
            quality_score=0.9,
            camera_id=None,
        )
    ]
    added: list[object] = []
    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(person),
                scalar_one_result(employee_cluster),
                scalar_all_result(embeddings),
            ]
        ),
    )
    auth = AuthContext(user_id="user-1", role="admin", tenant_id="tenant-1")

    service = IdentityReviewService(db)

    result = await service.assign_person_to_employee(
        auth=auth,
        source_person_id=person.id,
        employee_id=employee_id,
        reason="manager confirmed employee identity",
    )

    review = next(model for model in added if isinstance(model, IdentityMergeReview))

    assert result.cluster.id == employee_cluster.id
    assert person.identity_cluster_id == employee_cluster.id
    assert person.employee_id == employee_id
    assert person.recognition_state == "corrected"
    assert employee_cluster.face_template == pytest.approx([0.3, 0.3, 0.3])
    assert review.review_type == "assign_employee"
    assert review.target_employee_id == employee_id
    assert review.created_by == "user-1"


@pytest.mark.asyncio
async def test_undo_identity_review_marks_review_reverted_and_detaches_person():
    person = _unknown_person(person_id=uuid.uuid4(), cluster_id=uuid.uuid4())
    review = IdentityMergeReview(
        id=uuid.uuid4(),
        tenant_id="tenant-1",
        review_type="assign_employee",
        decision="confirmed",
        source_session_person_id=person.id,
        target_employee_id=uuid.uuid4(),
        target_cluster_id=person.identity_cluster_id,
        created_by="user-1",
    )
    person.employee_id = review.target_employee_id
    person.recognition_state = "corrected"

    db = SimpleNamespace(
        add=lambda model: None,
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(review),
                scalar_one_result(person),
            ]
        ),
    )
    auth = AuthContext(user_id="auditor-1", role="admin", tenant_id="tenant-1")

    service = IdentityReviewService(db)

    reverted = await service.undo_identity_review(
        auth=auth,
        review_id=review.id,
        reason="wrong match",
    )

    assert reverted.review.decision == "reverted"
    assert reverted.review.reverted_by == "auditor-1"
    assert reverted.person.identity_cluster_id is None
    assert reverted.person.employee_id is None
    assert reverted.person.recognition_state == "unknown"


@pytest.mark.asyncio
async def test_undo_unknown_merge_detaches_source_and_targets_from_cluster():
    cluster_id = uuid.uuid4()
    source_person = _unknown_person(person_id=uuid.uuid4(), cluster_id=cluster_id)
    target_person = _unknown_person(person_id=uuid.uuid4(), cluster_id=cluster_id)
    review = IdentityMergeReview(
        id=uuid.uuid4(),
        tenant_id="tenant-1",
        review_type="unknown_merge",
        decision="confirmed",
        source_session_person_id=source_person.id,
        target_cluster_id=cluster_id,
        evidence_snapshot={"target_person_ids": [str(target_person.id)]},
        created_by="user-1",
    )
    db = SimpleNamespace(
        add=lambda model: None,
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(review),
                scalar_one_result(source_person),
                scalar_all_result([target_person]),
            ]
        ),
    )
    auth = AuthContext(user_id="auditor-1", role="admin", tenant_id="tenant-1")

    service = IdentityReviewService(db)

    reverted = await service.undo_identity_review(
        auth=auth,
        review_id=review.id,
        reason="wrong anonymous merge",
    )

    assert reverted.person.id == source_person.id
    assert source_person.identity_cluster_id is None
    assert target_person.identity_cluster_id is None
    assert review.decision == "reverted"
