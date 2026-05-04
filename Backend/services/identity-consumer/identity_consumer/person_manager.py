"""Canonical person manager — creates, updates, and merges session persons.

This is the core identity service that maintains the session_persons table
as the single source of truth for who is in the scene.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.events import IdentityEventPayload, StreamNames
from airco.models import (
    EmployeeFaceTemplate,
    IdentityCluster,
    PersonEmbedding,
    SessionPerson,
    SessionPersonTrackBinding,
)
from airco.redis_streams import publish_event
from identity_consumer.state_machine import IdentityStateMachine
from identity_consumer.face_matcher import FaceMatcher
from identity_consumer.cross_camera import CrossCameraMerger
from identity_consumer.tracklet_aggregator import TrackletAggregator

logger = logging.getLogger(__name__)


class PersonManager:
    """Manages canonical session persons for a running session."""

    SAME_CAMERA_REASSOCIATION_WINDOW_SECONDS = 8.0
    CROSS_CAMERA_REASSOCIATION_WINDOW_SECONDS = 30.0
    # Minimum OSNet cosine similarity for a direct session-wide re-ID merge.
    # Strong body matches override timing windows — same person looks the same all session.
    BODY_EMBEDDING_DIRECT_MATCH_THRESHOLD = 0.70
    EMPLOYEE_CLUSTER_DIRECT_MATCH_THRESHOLD = 0.85
    # Diversity gate for gallery writes.  A new body embedding is only persisted
    # if it is sufficiently different from everything already in the gallery.
    # 0.92 = "at least 8% different" — skips redundant same-pose/same-angle captures
    # while accepting meaningful viewpoint changes.
    BODY_EMBEDDING_DIVERSITY_THRESHOLD = 0.92
    # Safety cap: even with diversity filtering, never store more than this many
    # body embeddings per person per session.  In practice diversity filtering
    # will keep the count well below this.
    BODY_EMBEDDING_GALLERY_MAX = 500

    def __init__(self, db: AsyncSession, face_matcher: FaceMatcher, merger: CrossCameraMerger):
        self.db = db
        self.face_matcher = face_matcher
        self.merger = merger
        self._track_bindings: dict[tuple, uuid.UUID] = {}
        self._state_machines: dict[uuid.UUID, IdentityStateMachine] = {}
        self._aggregator = TrackletAggregator()
        self._camera_thresholds: dict[uuid.UUID, dict] = {}

    async def load_active_bindings(self):
        stmt = select(SessionPersonTrackBinding).where(
            SessionPersonTrackBinding.binding_state == "active"
        )
        result = await self.db.execute(stmt)
        for binding in result.scalars().all():
            key = self._binding_key(binding.session_id, binding.camera_id, binding.track_id)
            self._track_bindings[key] = binding.session_person_id
            await self._ensure_state_machine(binding.session_person_id)

    async def handle_track_started(
        self, session_id: uuid.UUID, camera_id: uuid.UUID, track_id: int,
        bbox: list[float], confidence: float, timestamp: datetime,
        reid_profile: str | None = None,
    ) -> uuid.UUID:
        key = self._binding_key(session_id, camera_id, track_id)
        binding = await self._load_active_binding(session_id, camera_id, track_id)
        if binding is not None:
            person = await self._load_person(binding.session_person_id)
            if person is not None:
                binding.last_seen_at = timestamp
                person.last_seen_at = timestamp
                person.is_active = True
                self._apply_active_binding_snapshot(person, [binding])
            self._track_bindings[key] = binding.session_person_id
            await self._ensure_state_machine(binding.session_person_id, person=person)
            return binding.session_person_id

        same_camera_person = await self._resolve_recent_same_camera_reassociation(
            session_id=session_id,
            camera_id=camera_id,
            track_id=track_id,
            timestamp=timestamp,
        )
        if same_camera_person is not None:
            return await self._attach_track_to_person(
                person=same_camera_person,
                session_id=session_id,
                camera_id=camera_id,
                track_id=track_id,
                confidence=confidence,
                timestamp=timestamp,
                source="same_camera_reassoc",
            )

        cross_camera_person = await self._resolve_cross_camera_reassociation(
            session_id=session_id,
            camera_id=camera_id,
            confidence=confidence,
            timestamp=timestamp,
        )
        if cross_camera_person is not None:
            return await self._attach_track_to_person(
                person=cross_camera_person,
                session_id=session_id,
                camera_id=camera_id,
                track_id=track_id,
                confidence=confidence,
                timestamp=timestamp,
                source="cross_camera_reassoc",
            )

        person_id = uuid.uuid4()
        person = SessionPerson(
            id=person_id,
            session_id=session_id,
            tenant_id="default",
            recognition_state="unknown",
            display_name=f"Unknown Person {person_id.hex[:8].upper()}",
            current_cameras=[str(camera_id)],
            active_track_bindings=[{"camera_id": str(camera_id), "track_id": track_id}],
            first_seen_at=timestamp,
            last_seen_at=timestamp,
            is_active=True,
            evidence_summary={"face_hits": 0, "body_matches": 0, "reviewed": False},
        )
        binding = SessionPersonTrackBinding(
            session_id=session_id,
            session_person_id=person_id,
            camera_id=camera_id,
            track_id=track_id,
            binding_state="active",
            started_at=timestamp,
            last_seen_at=timestamp,
            source="direct_track",
            confidence=confidence,
            evidence_summary={},
        )
        self.db.add(person)
        self.db.add(binding)
        await self.db.flush()

        self._track_bindings[key] = person_id
        await self._ensure_state_machine(person_id, person=person)

        await publish_event(StreamNames.IDENTITY, IdentityEventPayload(
            event_type="person_created",
            session_id=session_id,
            session_person_id=person_id,
            recognition_state="unknown",
            track_id=track_id,
            camera_id=camera_id,
            timestamp=timestamp,
        ).to_redis())

        return person_id

    async def handle_track_observed(
        self, session_id: uuid.UUID, camera_id: uuid.UUID, track_id: int,
        bbox: list[float], timestamp: datetime,
    ):
        binding = await self._load_active_binding(session_id, camera_id, track_id)
        if binding is None:
            person_id = await self.handle_track_started(
                session_id, camera_id, track_id, bbox, 0.5, timestamp)
            self._track_bindings[self._binding_key(session_id, camera_id, track_id)] = person_id
            return

        person = await self._load_person(binding.session_person_id)
        binding.last_seen_at = timestamp
        self._track_bindings[self._binding_key(session_id, camera_id, track_id)] = binding.session_person_id
        await self._ensure_state_machine(binding.session_person_id, person=person)

        if person:
            person.last_seen_at = timestamp
            person.is_active = True

    async def handle_track_ended(
        self, session_id: uuid.UUID, camera_id: uuid.UUID, track_id: int,
        timestamp: datetime,
    ):
        key = self._binding_key(session_id, camera_id, track_id)
        binding = await self._load_active_binding(session_id, camera_id, track_id)
        if binding is None:
            self._track_bindings.pop(key, None)
            return

        binding.binding_state = "closed"
        binding.last_seen_at = timestamp
        binding.ended_at = timestamp
        self._track_bindings.pop(key, None)

        person = await self._load_person(binding.session_person_id)
        if person:
            remaining_bindings = await self._load_active_bindings_for_person(binding.session_person_id)
            self._apply_active_binding_snapshot(person, remaining_bindings)
            person.last_seen_at = timestamp
            person.is_active = bool(remaining_bindings)

    # Grace window for crops that arrive after track_ended closes the binding.
    # ByteTrack frequently ends tracks on occlusion and the crop event pipeline
    # can lag by 1-10 seconds.  We fall back to the most recently closed binding
    # for the same (session_id, camera_id, track_id) within this window so that
    # short-lived tracks still get their embeddings stored.
    CROP_LATE_ARRIVAL_GRACE_SECONDS = 30.0

    async def handle_crop(
        self, session_id: uuid.UUID, camera_id: uuid.UUID, track_id: int,
        embedding: list[float], quality_score: float, crop_type: str, timestamp: datetime,
        reid_profile: str | None = None,
    ):
        binding = await self._load_active_binding(session_id, camera_id, track_id)
        if binding is None:
            binding = await self._load_recently_closed_binding(
                session_id, camera_id, track_id, timestamp
            )
        if binding is None:
            return
        person_id = binding.session_person_id
        self._track_bindings[self._binding_key(session_id, camera_id, track_id)] = person_id

        person = None
        if crop_type == "body_crop":
            person = await self._load_person(person_id)
        if crop_type == "body_crop" and person is not None:
            matched_person = await self._resolve_body_embedding_reassociation(
                session_id=session_id,
                camera_id=camera_id,
                current_person_id=person_id,
                embedding=embedding,
                timestamp=timestamp,
            )
            if matched_person is not None and matched_person.id != person_id:
                await self._rebind_track_to_person(
                    binding=binding,
                    current_person=person,
                    target_person=matched_person,
                    session_id=session_id,
                    camera_id=camera_id,
                    track_id=track_id,
                    timestamp=timestamp,
                )
                person_id = matched_person.id
                person = matched_person
            elif getattr(person, "identity_cluster_id", None) is None:
                cluster_match = await self._resolve_identity_cluster_match(
                    tenant_id=person.tenant_id,
                    embedding=embedding,
                )
                if cluster_match is not None:
                    cluster, cluster_similarity = cluster_match
                    person.identity_cluster_id = cluster.id
                    if (
                        getattr(cluster, "employee_id", None) is not None
                        and cluster_similarity >= self.EMPLOYEE_CLUSTER_DIRECT_MATCH_THRESHOLD
                    ):
                        person.employee_id = cluster.employee_id
                        person.recognition_state = "corrected"

        import numpy as np

        # For body crops, only persist if this embedding adds new viewpoint diversity.
        # This prevents the gallery from being saturated by near-identical same-pose
        # captures (e.g. person sitting at desk for hours), while naturally accumulating
        # diverse angles as the person moves.  Face crops are always stored (rare, high
        # value for recognition).
        should_store = True
        if crop_type == "body_crop":
            should_store = await self._is_diverse_body_embedding(
                person_id=person_id,
                embedding=embedding,
            )

        if should_store:
            pe = PersonEmbedding(
                session_person_id=person_id,
                embedding_type="face" if crop_type == "face_crop" else "body",
                embedding=embedding,
                quality_score=quality_score,
                camera_id=camera_id,
                captured_at=timestamp,
            )
            self.db.add(pe)

        self._aggregator.update(
            person_id=person_id,
            modality="face" if crop_type == "face_crop" else "body",
            embedding=np.asarray(embedding, dtype=np.float32),
            quality=quality_score,
        )

        if person is not None and crop_type == "body_crop":
            person.body_confidence = max(float(getattr(person, "body_confidence", 0.0) or 0.0), quality_score)
            evidence_summary = person.evidence_summary or {}
            evidence_summary["body_matches"] = int(evidence_summary.get("body_matches", 0)) + 1
            person.evidence_summary = evidence_summary

        if crop_type != "face_crop":
            return

        templates = await self._load_employee_templates()
        cam_thresholds = await self._get_camera_thresholds(camera_id)
        matches = self.face_matcher.find_top_matches(
            query=np.array(embedding),
            templates=templates,
            top_k=3,
            accept_threshold=cam_thresholds.get("face_accept"),
            uncertain_threshold=cam_thresholds.get("face_uncertain"),
        )

        person = await self._load_person(person_id)
        if not person:
            return
        sm = await self._ensure_state_machine(person_id, person=person)

        if matches:
            best = matches[0]
            emp_id = uuid.UUID(best["employee_id"]) if isinstance(best["employee_id"], str) else best["employee_id"]
            obs_count = len(sm._evidence.get(emp_id, [])) + 1

            transition = sm.process_face_evidence(
                employee_id=emp_id,
                similarity=best["score"],
                observation_count=obs_count,
                band=best.get("band", "accept"),
            )

            if transition.new_state != person.recognition_state or transition.conflict:
                person.recognition_state = transition.new_state
                person.employee_id = sm.employee_id
                person.face_confidence = best["score"]

                if sm.employee_id:
                    emp_name = await self._get_employee_name(sm.employee_id)
                    person.display_name = emp_name or person.display_name

                es = person.evidence_summary or {}
                es["face_hits"] = es.get("face_hits", 0) + 1
                person.evidence_summary = es

                await publish_event(StreamNames.IDENTITY, IdentityEventPayload(
                    event_type=f"person_{transition.new_state}",
                    session_id=session_id,
                    session_person_id=person_id,
                    recognition_state=transition.new_state,
                    track_id=track_id,
                    employee_id=sm.employee_id,
                    employee_name=person.display_name,
                    camera_id=camera_id,
                    confidence=best["score"],
                    timestamp=timestamp,
                ).to_redis())

        if matches and person:
            best = matches[0]
            await self._maybe_auto_enroll_template(
                person=person,
                embedding=embedding,
                quality_score=quality_score,
                similarity=best["score"],
                camera_id=camera_id,
                session_id=session_id,
            )

        await self.db.flush()

    async def _load_employee_templates(self) -> dict[str, list]:
        import numpy as np
        stmt = select(EmployeeFaceTemplate)
        result = await self.db.execute(stmt)
        templates: dict[str, list] = {}
        for row in result.scalars():
            emp_key = str(row.employee_id)
            if emp_key not in templates:
                templates[emp_key] = []
            templates[emp_key].append(np.array(row.embedding, dtype=np.float32))
        return templates

    async def _get_employee_name(self, employee_id: uuid.UUID) -> str | None:
        from airco.models import Employee
        stmt = select(Employee.name).where(Employee.id == employee_id)
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        return row

    @staticmethod
    def _binding_key(session_id: uuid.UUID, camera_id: uuid.UUID, track_id: int) -> tuple[str, str, int]:
        return (str(session_id), str(camera_id), track_id)

    async def _load_active_binding(
        self,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        track_id: int,
    ) -> SessionPersonTrackBinding | None:
        stmt = select(SessionPersonTrackBinding).where(
            SessionPersonTrackBinding.session_id == session_id,
            SessionPersonTrackBinding.camera_id == camera_id,
            SessionPersonTrackBinding.track_id == track_id,
            SessionPersonTrackBinding.binding_state == "active",
        )
        result = await self.db.execute(stmt)
        binding = result.scalar_one_or_none()
        if binding is not None:
            self._track_bindings[self._binding_key(session_id, camera_id, track_id)] = binding.session_person_id
        return binding

    async def _load_recently_closed_binding(
        self,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        track_id: int,
        timestamp: datetime,
    ) -> SessionPersonTrackBinding | None:
        """Return the most recently closed binding for this exact track, if within
        CROP_LATE_ARRIVAL_GRACE_SECONDS.

        Crops frequently arrive after track_ended has already closed the binding
        (race condition in the Redis Streams consumer pipeline).  This lets us still
        store the embedding and run re-ID for the person rather than silently dropping it.
        """
        stmt = (
            select(SessionPersonTrackBinding)
            .where(
                SessionPersonTrackBinding.session_id == session_id,
                SessionPersonTrackBinding.camera_id == camera_id,
                SessionPersonTrackBinding.track_id == track_id,
                SessionPersonTrackBinding.binding_state == "closed",
            )
            .order_by(SessionPersonTrackBinding.ended_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        binding = result.scalar_one_or_none()
        if binding is None:
            return None
        ended_at = binding.ended_at or binding.last_seen_at
        if ended_at is None:
            return None
        elapsed = (timestamp - ended_at).total_seconds()
        if elapsed < 0 or elapsed > self.CROP_LATE_ARRIVAL_GRACE_SECONDS:
            return None
        return binding

    async def _load_recent_same_camera_bindings(
        self,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        timestamp: datetime,
    ) -> list[SessionPersonTrackBinding]:
        stmt = select(SessionPersonTrackBinding).where(
            SessionPersonTrackBinding.session_id == session_id,
            SessionPersonTrackBinding.camera_id == camera_id,
            SessionPersonTrackBinding.binding_state == "closed",
        )
        result = await self.db.execute(stmt)
        candidates: list[SessionPersonTrackBinding] = []
        for binding in result.scalars().all():
            last_seen_at = binding.last_seen_at or binding.ended_at
            if last_seen_at is None:
                continue
            elapsed_seconds = (timestamp - last_seen_at).total_seconds()
            if 0 <= elapsed_seconds <= self.SAME_CAMERA_REASSOCIATION_WINDOW_SECONDS:
                candidates.append(binding)
        return candidates

    async def _load_recent_cross_camera_bindings(
        self,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        timestamp: datetime,
    ) -> list[SessionPersonTrackBinding]:
        stmt = select(SessionPersonTrackBinding).where(
            SessionPersonTrackBinding.session_id == session_id,
            SessionPersonTrackBinding.camera_id != camera_id,
            or_(
                SessionPersonTrackBinding.binding_state == "active",
                SessionPersonTrackBinding.binding_state == "closed",
            ),
        )
        result = await self.db.execute(stmt)
        candidates: list[SessionPersonTrackBinding] = []
        for binding in result.scalars().all():
            last_seen_at = binding.last_seen_at or binding.ended_at
            if last_seen_at is None:
                continue
            elapsed_seconds = (timestamp - last_seen_at).total_seconds()
            if 0 <= elapsed_seconds <= self.CROSS_CAMERA_REASSOCIATION_WINDOW_SECONDS:
                candidates.append(binding)
        return candidates

    async def _load_active_bindings_for_person(
        self,
        session_person_id: uuid.UUID,
    ) -> list[SessionPersonTrackBinding]:
        stmt = select(SessionPersonTrackBinding).where(
            SessionPersonTrackBinding.session_person_id == session_person_id,
            SessionPersonTrackBinding.binding_state == "active",
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _load_person(self, person_id: uuid.UUID) -> SessionPerson | None:
        stmt = select(SessionPerson).where(SessionPerson.id == person_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_recent_same_camera_reassociation(
        self,
        *,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        track_id: int,
        timestamp: datetime,
    ) -> SessionPerson | None:
        recent_bindings = await self._load_recent_same_camera_bindings(session_id, camera_id, timestamp)
        candidate_ids = {
            binding.session_person_id
            for binding in recent_bindings
        }
        if len(candidate_ids) != 1:
            return None
        person = await self._load_person(next(iter(candidate_ids)))
        if person is None or getattr(person, "merged_into_session_person_id", None):
            return None
        return person

    async def _resolve_cross_camera_reassociation(
        self,
        *,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        confidence: float,
        timestamp: datetime,
    ) -> SessionPerson | None:
        recent_bindings = await self._load_recent_cross_camera_bindings(session_id, camera_id, timestamp)
        if not recent_bindings:
            return None

        candidates: list[tuple[float, SessionPerson]] = []
        seen_person_ids: set[uuid.UUID] = set()
        for binding in recent_bindings:
            if binding.session_person_id in seen_person_ids:
                continue
            person = await self._load_person(binding.session_person_id)
            if person is None or getattr(person, "merged_into_session_person_id", None):
                continue

            last_seen_at = binding.last_seen_at or binding.ended_at
            if last_seen_at is None:
                continue
            score = self.merger.compute_merge_score(
                body_similarity=confidence,
                face_similarity=person.face_confidence or 0.0,
                elapsed_seconds=max(0.0, (timestamp - last_seen_at).total_seconds()),
                camera_a=str(binding.camera_id),
                camera_b=str(camera_id),
            )
            candidates.append((score, person))
            seen_person_ids.add(binding.session_person_id)

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_person = candidates[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else None
        if not self.merger.is_confident_reassociation(best_score, runner_up_score=runner_up):
            return None
        return best_person

    async def _load_session_gallery_person_ids(
        self,
        *,
        session_id: uuid.UUID,
        current_person_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Return all non-merged SessionPerson IDs for this session except current_person_id.

        This is the session-wide gallery used for body re-ID.  Time windows are NOT
        applied here — the same person looks the same all session regardless of when
        their last track was active.
        """
        stmt = select(SessionPerson.id).where(
            SessionPerson.session_id == session_id,
            SessionPerson.id != current_person_id,
            SessionPerson.merged_into_session_person_id.is_(None),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _is_diverse_body_embedding(
        self,
        *,
        person_id: uuid.UUID,
        embedding,
    ) -> bool:
        """Return True if this body embedding is meaningfully different from the
        existing gallery — i.e. worth storing.

        A new embedding is considered redundant if its cosine similarity with any
        existing gallery embedding exceeds BODY_EMBEDDING_DIVERSITY_THRESHOLD (0.92).
        Redundant embeddings are skipped to avoid saturating the gallery with
        near-identical same-pose captures.
        """
        import numpy as np

        galleries = await self._load_recent_person_embeddings(
            person_ids=[person_id],
            embedding_type="body",
        )
        existing = galleries.get(person_id, [])

        # First embedding for this person is always stored.
        if not existing:
            return True

        # Safety cap: if gallery is already at max, stop storing.
        if len(existing) >= self.BODY_EMBEDDING_GALLERY_MAX:
            return False

        query = np.asarray(embedding, dtype=np.float32)
        max_sim = max(
            FaceMatcher.cosine_similarity(query, np.asarray(e, dtype=np.float32))
            for e in existing
        )
        return max_sim < self.BODY_EMBEDDING_DIVERSITY_THRESHOLD

    async def _load_recent_person_embeddings(
        self,
        *,
        person_ids: list[uuid.UUID],
        embedding_type: str,
        limit_per_person: int = 50,
    ) -> dict[uuid.UUID, list]:
        if not person_ids:
            return {}
        stmt = (
            select(PersonEmbedding)
            .where(
                PersonEmbedding.session_person_id.in_(person_ids),
                PersonEmbedding.embedding_type == embedding_type,
            )
            .order_by(PersonEmbedding.captured_at.desc())
        )
        result = await self.db.execute(stmt)
        galleries: dict[uuid.UUID, list] = {}
        for row in result.scalars().all():
            gallery = galleries.setdefault(row.session_person_id, [])
            if len(gallery) >= limit_per_person:
                continue
            gallery.append(row.embedding)
        return galleries

    async def _resolve_body_embedding_reassociation(
        self,
        *,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        current_person_id: uuid.UUID,
        embedding,
        timestamp: datetime,
    ) -> SessionPerson | None:
        """Match a body embedding against the session-wide gallery.

        Unlike the track_started path, we have a real OSNet embedding here, so
        timing windows are irrelevant — the same person looks the same throughout
        the session.  A direct cosine similarity threshold is used instead of the
        timing-penalized merger score.
        """
        import numpy as np

        person_ids = await self._load_session_gallery_person_ids(
            session_id=session_id,
            current_person_id=current_person_id,
        )
        if not person_ids:
            return None

        galleries = await self._load_recent_person_embeddings(
            person_ids=person_ids,
            embedding_type="body",
        )
        if not galleries:
            return None

        query_embedding = np.asarray(embedding, dtype=np.float32)
        candidates: list[tuple[float, SessionPerson]] = []

        for person_id, gallery in galleries.items():
            if not gallery:
                continue

            person = await self._load_person(person_id)
            if person is None or getattr(person, "merged_into_session_person_id", None):
                continue

            if await self._has_co_occurrence_contradiction(current_person_id, person_id):
                continue

            # Prefer EMA template if available (faster, more stable)
            template = self._aggregator.get_template(person_id, "body")
            if template is not None and template.update_count >= 3:
                body_similarity = FaceMatcher.cosine_similarity(
                    query_embedding, template.embedding,
                )
            else:
                body_similarity = max(
                    FaceMatcher.cosine_similarity(query_embedding, np.asarray(candidate, dtype=np.float32))
                    for candidate in gallery
                )
            candidates.append((body_similarity, person))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_person = candidates[0]
        runner_up_score = candidates[1][0] if len(candidates) > 1 else None

        if best_score < self.BODY_EMBEDDING_DIRECT_MATCH_THRESHOLD:
            return None
        if runner_up_score is not None and (best_score - runner_up_score) < self.merger.REASSOCIATION_MARGIN:
            return None
        return best_person

    async def _resolve_identity_cluster_match(
        self,
        *,
        tenant_id: str,
        embedding,
    ) -> tuple[IdentityCluster, float] | None:
        import numpy as np

        stmt = select(IdentityCluster).where(
            IdentityCluster.tenant_id == tenant_id,
            IdentityCluster.cluster_state != "superseded",
        )
        result = await self.db.execute(stmt)
        clusters = list(result.scalars().all())
        if not clusters:
            return None

        query_embedding = np.asarray(embedding, dtype=np.float32)
        candidates: list[tuple[float, IdentityCluster]] = []
        for cluster in clusters:
            body_template = getattr(cluster, "body_template", None)
            if not body_template:
                continue
            similarity = FaceMatcher.cosine_similarity(
                query_embedding,
                np.asarray(body_template, dtype=np.float32),
            )
            candidates.append((similarity, cluster))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_cluster = candidates[0]
        runner_up_score = candidates[1][0] if len(candidates) > 1 else None
        if best_score < self.BODY_EMBEDDING_DIRECT_MATCH_THRESHOLD:
            return None
        if runner_up_score is not None and (best_score - runner_up_score) < self.merger.REASSOCIATION_MARGIN:
            return None
        return best_cluster, best_score

    async def _rebind_track_to_person(
        self,
        *,
        binding: SessionPersonTrackBinding,
        current_person: SessionPerson,
        target_person: SessionPerson,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        track_id: int,
        timestamp: datetime,
    ) -> None:
        binding.session_person_id = target_person.id
        binding.source = "embedding_reassoc"
        binding.last_seen_at = timestamp

        existing_target_bindings = await self._load_active_bindings_for_person(target_person.id)
        target_person.last_seen_at = timestamp
        target_person.is_active = True
        self._apply_active_binding_snapshot(
            target_person,
            [*existing_target_bindings, binding],
        )

        current_person.last_seen_at = timestamp
        current_person.is_active = False
        current_person.current_cameras = []
        current_person.active_track_bindings = []
        current_person.merged_into_session_person_id = target_person.id

        key = self._binding_key(session_id, camera_id, track_id)
        self._track_bindings[key] = target_person.id
        await self._ensure_state_machine(target_person.id, person=target_person)

    async def _ensure_state_machine(
        self,
        person_id: uuid.UUID,
        *,
        person: SessionPerson | None = None,
    ) -> IdentityStateMachine | None:
        existing = self._state_machines.get(person_id)
        if existing is not None:
            return existing

        if person is None:
            person = await self._load_person(person_id)
        if person is None:
            return None

        sm = IdentityStateMachine()
        sm._state = person.recognition_state
        sm._employee_id = person.employee_id
        self._state_machines[person_id] = sm
        return sm

    async def _attach_track_to_person(
        self,
        *,
        person: SessionPerson,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        track_id: int,
        confidence: float,
        timestamp: datetime,
        source: str,
    ) -> uuid.UUID:
        binding = SessionPersonTrackBinding(
            session_id=session_id,
            session_person_id=person.id,
            camera_id=camera_id,
            track_id=track_id,
            binding_state="active",
            started_at=timestamp,
            last_seen_at=timestamp,
            source=source,
            confidence=confidence,
            evidence_summary={},
        )
        self.db.add(binding)

        existing_bindings = await self._load_active_bindings_for_person(person.id)
        person.last_seen_at = timestamp
        person.is_active = True
        self._apply_active_binding_snapshot(person, [*existing_bindings, binding])

        await self.db.flush()

        key = self._binding_key(session_id, camera_id, track_id)
        self._track_bindings[key] = person.id
        await self._ensure_state_machine(person.id, person=person)
        return person.id

    async def _load_bindings_for_persons(
        self, person_ids: list[uuid.UUID]
    ) -> list[SessionPersonTrackBinding]:
        """Load all track bindings (active and closed) for the given person IDs."""
        if not person_ids:
            return []
        stmt = select(SessionPersonTrackBinding).where(
            SessionPersonTrackBinding.session_person_id.in_(person_ids),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _has_co_occurrence_contradiction(
        self, person_a_id: uuid.UUID, person_b_id: uuid.UUID
    ) -> bool:
        """Return True if person_a and person_b had overlapping active track
        bindings on the same camera at any point — proving they are different people."""
        bindings = await self._load_bindings_for_persons([person_a_id, person_b_id])
        a_bindings = [b for b in bindings if b.session_person_id == person_a_id]
        b_bindings = [b for b in bindings if b.session_person_id == person_b_id]

        for ba in a_bindings:
            for bb in b_bindings:
                if ba.camera_id != bb.camera_id:
                    continue
                a_start = ba.started_at
                a_end = ba.ended_at or ba.last_seen_at
                b_start = bb.started_at
                b_end = bb.ended_at or bb.last_seen_at
                if a_start is None or a_end is None or b_start is None or b_end is None:
                    continue
                if a_start <= b_end and b_start <= a_end:
                    return True
        return False

    MAX_TEMPLATES_PER_EMPLOYEE = 30
    AUTO_ENROLL_MIN_SIMILARITY = 0.80
    AUTO_ENROLL_MIN_QUALITY = 0.70

    async def _maybe_auto_enroll_template(
        self,
        *,
        person: SessionPerson,
        embedding: list[float],
        quality_score: float,
        similarity: float,
        camera_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> None:
        """Auto-enroll a face embedding as an employee template if confidence is high."""
        if person.recognition_state != "identified":
            return
        if person.employee_id is None:
            return
        if similarity < self.AUTO_ENROLL_MIN_SIMILARITY:
            return
        if quality_score < self.AUTO_ENROLL_MIN_QUALITY:
            return

        stmt = (
            select(EmployeeFaceTemplate)
            .where(
                EmployeeFaceTemplate.employee_id == person.employee_id,
                EmployeeFaceTemplate.is_active == True,
            )
            .order_by(EmployeeFaceTemplate.quality_score.asc())
        )
        result = await self.db.execute(stmt)
        existing = list(result.scalars().all())

        if len(existing) >= self.MAX_TEMPLATES_PER_EMPLOYEE:
            worst = existing[0]
            worst.is_active = False

        max_version = max((getattr(t, 'version', 0) or 0 for t in existing), default=0)

        new_template = EmployeeFaceTemplate(
            employee_id=person.employee_id,
            embedding=embedding,
            quality_score=quality_score,
            source_camera_id=camera_id,
            source_session_id=session_id,
            capture_date=datetime.now(timezone.utc),
            is_active=True,
            version=max_version + 1,
        )
        self.db.add(new_template)

    async def _get_camera_thresholds(self, camera_id: uuid.UUID) -> dict:
        """Load per-camera threshold overrides, cached."""
        if camera_id in self._camera_thresholds:
            return self._camera_thresholds[camera_id]

        from airco.models import Camera
        stmt = select(Camera).where(Camera.id == camera_id)
        result = await self.db.execute(stmt)
        camera = result.scalar_one_or_none()

        thresholds = {}
        if camera is not None:
            if getattr(camera, "face_accept_threshold", None) is not None:
                thresholds["face_accept"] = camera.face_accept_threshold
            if getattr(camera, "face_uncertain_threshold", None) is not None:
                thresholds["face_uncertain"] = camera.face_uncertain_threshold
            if getattr(camera, "body_accept_threshold", None) is not None:
                thresholds["body_accept"] = camera.body_accept_threshold
            if getattr(camera, "body_uncertain_threshold", None) is not None:
                thresholds["body_uncertain"] = camera.body_uncertain_threshold

        self._camera_thresholds[camera_id] = thresholds
        return thresholds

    def _apply_active_binding_snapshot(
        self,
        person: SessionPerson,
        bindings: list[SessionPersonTrackBinding],
    ) -> None:
        person.active_track_bindings = [
            {"camera_id": str(binding.camera_id), "track_id": binding.track_id}
            for binding in bindings
        ]
        cameras: list[str] = []
        for binding in bindings:
            camera_id = str(binding.camera_id)
            if camera_id not in cameras:
                cameras.append(camera_id)
        person.current_cameras = cameras
