"""Domain service for human-in-the-loop identity review actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthContext
from airco.models import (
    IdentityCluster,
    IdentityClusterMember,
    IdentityMergeReview,
    PersonEmbedding,
    SessionPerson,
    Session,
)


@dataclass
class MergeReviewResult:
    cluster: IdentityCluster
    review: IdentityMergeReview
    persons: list[SessionPerson]


@dataclass
class UndoReviewResult:
    review: IdentityMergeReview
    person: SessionPerson


class IdentityReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_review_queue(
        self,
        *,
        auth: AuthContext,
        scope: str,
        session_id: uuid.UUID | None,
    ) -> list[dict]:
        if scope == "active_session":
            if session_id is None:
                return []
            session = await self._load_session(session_id)
            people = await self._load_unknown_people_for_session(session_id=session_id)
            return [
                self._queue_item_for_active_person(
                    session=session,
                    person=person,
                    candidate_count=max(len(people) - 1, 0),
                )
                for person in people
            ]

        if scope == "cross_session":
            people = await self._load_unknown_people_for_tenant(tenant_id=auth.tenant_id)
            clusters = await self._load_identity_clusters(tenant_id=auth.tenant_id)
            if not people or not clusters:
                return []
            cluster = clusters[0]
            return [
                self._queue_item_for_cross_session_person(person=person, cluster=cluster)
                for person in people
            ]

        return []

    async def get_review_item(
        self,
        *,
        auth: AuthContext,
        item_id: str,
    ) -> dict | None:
        if item_id.startswith("active:"):
            person_id = self._parse_uuid_segment(item_id, expected_prefix="active", index=1)
            person = await self._load_person(person_id)
            session = await self._load_session(person.session_id)
            candidates = [
                candidate
                for candidate in await self._load_unknown_people_for_session(session_id=person.session_id)
                if candidate.id != person.id
            ]
            return {
                "review_item_id": item_id,
                "scope": "active_session",
                "kind": "merge_suggestion",
                "status": "open",
                "source": self._person_detail_payload(person, session_name=getattr(session, "name", None)),
                "candidates": [self._candidate_payload(candidate) for candidate in candidates[:5]],
                "history": [],
            }

        if item_id.startswith("cross:"):
            person_id = self._parse_uuid_segment(item_id, expected_prefix="cross", index=1)
            cluster_id = self._parse_uuid_segment(item_id, expected_prefix="cross", index=2)
            person = await self._load_person(person_id)
            cluster = await self._load_cluster(cluster_id)
            return {
                "review_item_id": item_id,
                "scope": "cross_session",
                "kind": "cluster_candidate",
                "status": "open",
                "source": self._person_detail_payload(person, session_name=None),
                "candidates": [self._cluster_candidate_payload(cluster)],
                "history": [],
            }

        return None

    async def list_review_history(
        self,
        *,
        auth: AuthContext,
    ) -> list[dict]:
        result = await self.db.execute(
            select(IdentityMergeReview)
            .where(IdentityMergeReview.tenant_id == auth.tenant_id)
            .order_by(IdentityMergeReview.created_at.desc())
        )
        reviews = result.scalars().all()
        return [
            {
                "review_id": str(review.id),
                "review_type": review.review_type,
                "decision": review.decision,
                "source_session_person_id": str(review.source_session_person_id),
                "target_session_person_id": (
                    str(review.target_session_person_id) if review.target_session_person_id else None
                ),
                "target_employee_id": str(review.target_employee_id) if review.target_employee_id else None,
                "source_cluster_id": str(review.source_cluster_id) if review.source_cluster_id else None,
                "target_cluster_id": str(review.target_cluster_id) if review.target_cluster_id else None,
                "reason": review.reason,
                "created_at": _iso(review.created_at),
                "reverted_at": _iso(review.reverted_at),
            }
            for review in reviews
        ]

    async def merge_unknown_persons(
        self,
        *,
        auth: AuthContext,
        source_person_id: uuid.UUID,
        target_person_ids: list[uuid.UUID],
        reason: str | None = None,
    ) -> MergeReviewResult:
        source_person = await self._load_person(source_person_id)
        target_people = await self._load_people(target_person_ids)
        people = [source_person, *target_people]

        cluster = await self._resolve_or_create_target_cluster(
            auth=auth,
            existing_cluster_ids=[person.identity_cluster_id for person in people],
            employee_id=None,
            cluster_state="anonymous",
            display_label="Merged Anonymous Identity",
        )

        for index, person in enumerate(people):
            person.identity_cluster_id = cluster.id
            self.db.add(
                IdentityClusterMember(
                    identity_cluster_id=cluster.id,
                    session_person_id=person.id,
                    member_role="seed" if index == 0 else "merged",
                    active=True,
                )
            )

        embeddings = await self._load_embeddings([person.id for person in people])
        self._apply_cluster_templates(cluster, embeddings)

        review = IdentityMergeReview(
            tenant_id=auth.tenant_id,
            review_type="unknown_merge",
            decision="confirmed",
            source_session_person_id=source_person.id,
            target_session_person_id=target_people[0].id if target_people else None,
            source_cluster_id=source_person.identity_cluster_id,
            target_cluster_id=cluster.id,
            reason=reason,
            evidence_snapshot={"target_person_ids": [str(person.id) for person in target_people]},
            created_by=auth.user_id,
        )
        self.db.add(review)
        await self.db.flush()
        return MergeReviewResult(cluster=cluster, review=review, persons=people)

    async def assign_person_to_employee(
        self,
        *,
        auth: AuthContext,
        source_person_id: uuid.UUID,
        employee_id: uuid.UUID,
        reason: str | None = None,
    ) -> MergeReviewResult:
        person = await self._load_person(source_person_id)
        cluster = await self._resolve_or_create_target_cluster(
            auth=auth,
            existing_cluster_ids=[],
            employee_id=employee_id,
            cluster_state="employee_linked",
            display_label="Employee Identity Cluster",
        )
        person.identity_cluster_id = cluster.id
        person.employee_id = employee_id
        person.recognition_state = "corrected"

        self.db.add(
            IdentityClusterMember(
                identity_cluster_id=cluster.id,
                session_person_id=person.id,
                member_role="employee_assignment",
                active=True,
            )
        )

        embeddings = await self._load_embeddings([person.id])
        self._apply_cluster_templates(cluster, embeddings)

        review = IdentityMergeReview(
            tenant_id=auth.tenant_id,
            review_type="assign_employee",
            decision="confirmed",
            source_session_person_id=person.id,
            target_employee_id=employee_id,
            source_cluster_id=person.identity_cluster_id,
            target_cluster_id=cluster.id,
            reason=reason,
            evidence_snapshot={},
            created_by=auth.user_id,
        )
        self.db.add(review)
        await self.db.flush()
        return MergeReviewResult(cluster=cluster, review=review, persons=[person])

    async def undo_identity_review(
        self,
        *,
        auth: AuthContext,
        review_id: uuid.UUID,
        reason: str | None = None,
    ) -> UndoReviewResult:
        review = await self._load_review(review_id)
        person = await self._load_person(review.source_session_person_id)

        review.decision = "reverted"
        review.reverted_by = auth.user_id
        review.reverted_at = datetime.now(timezone.utc)
        if reason:
            review.evidence_snapshot = {**(review.evidence_snapshot or {}), "undo_reason": reason}

        if review.review_type == "assign_employee":
            person.identity_cluster_id = None
            person.employee_id = None
            person.recognition_state = "unknown"
        elif review.review_type == "unknown_merge":
            person.identity_cluster_id = None
            target_ids = [
                uuid.UUID(person_id)
                for person_id in (review.evidence_snapshot or {}).get("target_person_ids", [])
            ]
            for target_person in await self._load_people(target_ids):
                target_person.identity_cluster_id = None
        else:
            person.identity_cluster_id = None

        await self.db.flush()
        return UndoReviewResult(review=review, person=person)

    async def _load_person(self, person_id: uuid.UUID) -> SessionPerson:
        result = await self.db.execute(select(SessionPerson).where(SessionPerson.id == person_id))
        person = result.scalar_one_or_none()
        if person is None:
            raise ValueError(f"SessionPerson {person_id} not found")
        return person

    async def _load_session(self, session_id: uuid.UUID) -> Session:
        result = await self.db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        return session

    async def _load_people(self, person_ids: list[uuid.UUID]) -> list[SessionPerson]:
        if not person_ids:
            return []
        result = await self.db.execute(select(SessionPerson).where(SessionPerson.id.in_(person_ids)))
        return result.scalars().all()

    async def _load_review(self, review_id: uuid.UUID) -> IdentityMergeReview:
        result = await self.db.execute(select(IdentityMergeReview).where(IdentityMergeReview.id == review_id))
        review = result.scalar_one_or_none()
        if review is None:
            raise ValueError(f"IdentityMergeReview {review_id} not found")
        return review

    async def _load_cluster(self, cluster_id: uuid.UUID) -> IdentityCluster:
        result = await self.db.execute(select(IdentityCluster).where(IdentityCluster.id == cluster_id))
        cluster = result.scalar_one_or_none()
        if cluster is None:
            raise ValueError(f"IdentityCluster {cluster_id} not found")
        return cluster

    async def _load_embeddings(self, person_ids: list[uuid.UUID]) -> list[PersonEmbedding]:
        result = await self.db.execute(select(PersonEmbedding).where(PersonEmbedding.session_person_id.in_(person_ids)))
        return result.scalars().all()

    async def _load_unknown_people_for_session(self, *, session_id: uuid.UUID) -> list[SessionPerson]:
        result = await self.db.execute(
            select(SessionPerson).where(
                SessionPerson.session_id == session_id,
                SessionPerson.recognition_state == "unknown",
                SessionPerson.merged_into_session_person_id.is_(None),
            )
        )
        return result.scalars().all()

    async def _load_unknown_people_for_tenant(self, *, tenant_id: str) -> list[SessionPerson]:
        result = await self.db.execute(
            select(SessionPerson).where(
                SessionPerson.tenant_id == tenant_id,
                SessionPerson.recognition_state == "unknown",
                SessionPerson.merged_into_session_person_id.is_(None),
            )
        )
        return result.scalars().all()

    async def _load_identity_clusters(self, *, tenant_id: str) -> list[IdentityCluster]:
        result = await self.db.execute(
            select(IdentityCluster).where(
                IdentityCluster.tenant_id == tenant_id,
                IdentityCluster.cluster_state != "superseded",
            )
        )
        return result.scalars().all()

    async def _resolve_or_create_target_cluster(
        self,
        *,
        auth: AuthContext,
        existing_cluster_ids: list[uuid.UUID | None],
        employee_id: uuid.UUID | None,
        cluster_state: str,
        display_label: str,
    ) -> IdentityCluster:
        for cluster_id in existing_cluster_ids:
            if cluster_id is None:
                continue
            result = await self.db.execute(select(IdentityCluster).where(IdentityCluster.id == cluster_id))
            cluster = result.scalar_one_or_none()
            if cluster is not None:
                return cluster

        if employee_id is not None:
            result = await self.db.execute(
                select(IdentityCluster).where(IdentityCluster.employee_id == employee_id)
            )
            cluster = result.scalar_one_or_none()
            if cluster is not None:
                return cluster

        cluster = IdentityCluster(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            employee_id=employee_id,
            cluster_state=cluster_state,
            display_label=display_label,
            evidence_summary={},
        )
        self.db.add(cluster)
        return cluster

    def _apply_cluster_templates(self, cluster: IdentityCluster, embeddings: list[PersonEmbedding]) -> None:
        face_vectors = [embedding.embedding for embedding in embeddings if embedding.embedding_type == "face"]
        body_vectors = [embedding.embedding for embedding in embeddings if embedding.embedding_type == "body"]
        if face_vectors:
            cluster.face_template = self._average_vectors(face_vectors)
            cluster.face_template_updates = len(face_vectors)
        if body_vectors:
            cluster.body_template = self._average_vectors(body_vectors)
            cluster.body_template_updates = len(body_vectors)

    @staticmethod
    def _average_vectors(vectors: list[list[float]]) -> list[float]:
        if not vectors:
            return []
        dimensions = len(vectors[0])
        return [
            round(sum(vector[index] for vector in vectors) / len(vectors), 6)
            for index in range(dimensions)
        ]

    def _queue_item_for_active_person(
        self,
        *,
        session: Session,
        person: SessionPerson,
        candidate_count: int,
    ) -> dict:
        return {
            "review_item_id": f"active:{person.id}",
            "scope": "active_session",
            "kind": "merge_suggestion",
            "status": "open",
            "session_id": str(session.id),
            "session_name": getattr(session, "name", None),
            "source_person_id": str(person.id),
            "source_cluster_id": str(person.identity_cluster_id) if person.identity_cluster_id else None,
            "representative_thumbnail_url": getattr(person, "best_thumbnail_url", None),
            "display_name": getattr(person, "display_name", None),
            "confidence": float(getattr(person, "body_confidence", 0.0) or 0.0),
            "candidate_count": candidate_count,
            "reason_tags": ["same_session_unknown", "review_identity_evidence"],
            "created_at": _iso(getattr(person, "first_seen_at", None)),
            "last_seen_at": _iso(getattr(person, "last_seen_at", None)),
        }

    def _queue_item_for_cross_session_person(
        self,
        *,
        person: SessionPerson,
        cluster: IdentityCluster,
    ) -> dict:
        return {
            "review_item_id": f"cross:{person.id}:{cluster.id}",
            "scope": "cross_session",
            "kind": "cluster_candidate",
            "status": "open",
            "session_id": str(person.session_id),
            "session_name": None,
            "source_person_id": str(person.id),
            "source_cluster_id": str(cluster.id),
            "representative_thumbnail_url": getattr(person, "best_thumbnail_url", None),
            "display_name": getattr(person, "display_name", None),
            "confidence": float(getattr(person, "body_confidence", 0.0) or 0.0),
            "candidate_count": 1,
            "reason_tags": ["anonymous_cluster_match", "cross_session_review"],
            "created_at": _iso(getattr(person, "first_seen_at", None)),
            "last_seen_at": _iso(getattr(person, "last_seen_at", None)),
        }

    def _person_detail_payload(
        self,
        person: SessionPerson,
        *,
        session_name: str | None,
    ) -> dict:
        return {
            "person_id": str(person.id),
            "display_name": getattr(person, "display_name", None),
            "best_thumbnail_url": getattr(person, "best_thumbnail_url", None),
            "session_id": str(person.session_id),
            "session_name": session_name,
            "identity_cluster_id": str(person.identity_cluster_id) if person.identity_cluster_id else None,
            "recognition_state": getattr(person, "recognition_state", None),
            "confidence": float(getattr(person, "body_confidence", 0.0) or 0.0),
            "reason_tags": ["review_identity_evidence"],
            "first_seen_at": _iso(getattr(person, "first_seen_at", None)),
            "last_seen_at": _iso(getattr(person, "last_seen_at", None)),
        }

    def _candidate_payload(self, person: SessionPerson) -> dict:
        return {
            "person_id": str(person.id),
            "display_name": getattr(person, "display_name", None),
            "best_thumbnail_url": getattr(person, "best_thumbnail_url", None),
            "confidence": float(getattr(person, "body_confidence", 0.0) or 0.0),
            "identity_cluster_id": str(person.identity_cluster_id) if person.identity_cluster_id else None,
            "reason_tags": ["same_session_unknown"],
        }

    def _cluster_candidate_payload(self, cluster: IdentityCluster) -> dict:
        return {
            "cluster_id": str(cluster.id),
            "display_name": getattr(cluster, "display_label", None),
            "best_thumbnail_url": getattr(cluster, "best_thumbnail_url", None),
            "employee_id": str(cluster.employee_id) if cluster.employee_id else None,
            "cluster_state": getattr(cluster, "cluster_state", None),
            "confidence": 0.0,
            "reason_tags": ["anonymous_cluster_match"],
        }

    @staticmethod
    def _parse_uuid_segment(item_id: str, *, expected_prefix: str, index: int) -> uuid.UUID:
        parts = item_id.split(":")
        if len(parts) <= index or parts[0] != expected_prefix:
            raise ValueError(f"Invalid review item id: {item_id}")
        return uuid.UUID(parts[index])


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
