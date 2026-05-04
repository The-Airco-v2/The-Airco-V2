"""Canonical ID bridge from Ultimate internal identities to v2 session persons."""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.models import SessionPerson, SessionPersonTrackBinding
from ultimate_adapter.config import session_person_map_key
from ultimate_adapter.ultimate_core import UltimateTrackingUpdate


def _merge_track_binding(bindings: list[dict] | None, camera_id: uuid.UUID, track_id: int) -> list[dict]:
    normalized = list(bindings or [])
    binding = {"camera_id": str(camera_id), "track_id": int(track_id)}
    if binding not in normalized:
        normalized.append(binding)
    return normalized


def _remove_track_binding(bindings: list[dict] | None, camera_id: uuid.UUID, track_id: int) -> list[dict]:
    target_camera = str(camera_id)
    return [
        binding
        for binding in (bindings or [])
        if not (
            str(binding.get("camera_id")) == target_camera
            and int(binding.get("track_id", -1)) == int(track_id)
        )
    ]


def _current_cameras_from_bindings(bindings: list[dict] | None) -> list[str]:
    return sorted({str(binding.get("camera_id")) for binding in (bindings or []) if binding.get("camera_id")})


def _coerce_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _unknown_display_name(person_id: uuid.UUID) -> str:
    return f"Unknown Person {person_id.hex[:8].upper()}"


@dataclass(frozen=True)
class CanonicalTrackObservation:
    session_person_id: uuid.UUID
    track_event_type: str
    person_created: bool
    recognition_state: str = "unknown"


@dataclass(frozen=True)
class CanonicalTrackClosure:
    session_person_id: uuid.UUID | None
    closed: bool


class UltimateCanonicalIdBridge:
    """Persist canonical SessionPerson state for Ultimate global IDs."""

    def __init__(self, redis_client, *, tenant_id: str = "default"):
        self.redis = redis_client
        self.tenant_id = tenant_id

    async def record_track_observation(
        self,
        db,
        *,
        update: UltimateTrackingUpdate,
        observed_at: datetime,
    ) -> CanonicalTrackObservation | None:
        track_id = update.track.track_id
        if track_id is None:
            return None

        session_id = _coerce_uuid(update.session_id)
        camera_id = _coerce_uuid(update.camera_id)
        global_id = int(update.identity.global_id)
        person, person_created = await self._resolve_or_create_person(
            db,
            session_id=session_id,
            camera_id=camera_id,
            global_id=global_id,
            observed_at=observed_at,
        )

        binding = await self._load_active_binding(
            db,
            session_id=session_id,
            camera_id=camera_id,
            track_id=int(track_id),
        )
        track_event_type = "track_observed"
        if binding is None:
            binding = SessionPersonTrackBinding(
                session_id=session_id,
                session_person_id=person.id,
                camera_id=camera_id,
                track_id=int(track_id),
                binding_state="active",
                started_at=observed_at,
                last_seen_at=observed_at,
                source="ultimate_track",
                confidence=float(update.track.confidence),
                evidence_summary={
                    "ultimate_global_id": global_id,
                    "ultimate_stage": update.identity.stage,
                },
            )
            db.add(binding)
            track_event_type = "track_started"
        else:
            binding.session_person_id = person.id
            binding.last_seen_at = observed_at
            binding.confidence = float(update.track.confidence)
            binding.source = "ultimate_track"
            binding.evidence_summary = {
                **(binding.evidence_summary or {}),
                "ultimate_global_id": global_id,
                "ultimate_stage": update.identity.stage,
            }

        person.last_seen_at = observed_at
        person.is_active = True
        person.active_track_bindings = _merge_track_binding(
            person.active_track_bindings,
            camera_id,
            int(track_id),
        )
        person.current_cameras = _current_cameras_from_bindings(person.active_track_bindings)
        person.evidence_summary = {
            **(person.evidence_summary or {}),
            "source": "ultimate",
            "ultimate_global_id": global_id,
            "ultimate_stage": update.identity.stage,
            "ultimate_last_camera": str(camera_id),
            "ultimate_last_track_id": int(track_id),
        }
        await db.flush()

        return CanonicalTrackObservation(
            session_person_id=person.id,
            track_event_type=track_event_type,
            person_created=person_created,
        )

    async def close_track(
        self,
        db,
        *,
        session_id: uuid.UUID | str,
        camera_id: uuid.UUID | str,
        track_id: int,
        observed_at: datetime,
    ) -> CanonicalTrackClosure:
        session_uuid = _coerce_uuid(session_id)
        camera_uuid = _coerce_uuid(camera_id)
        binding = await self._load_active_binding(
            db,
            session_id=session_uuid,
            camera_id=camera_uuid,
            track_id=int(track_id),
        )
        if binding is None:
            return CanonicalTrackClosure(session_person_id=None, closed=False)

        binding.binding_state = "closed"
        binding.last_seen_at = observed_at
        binding.ended_at = observed_at

        person = await self._load_person(db, binding.session_person_id)
        if person is not None:
            person.active_track_bindings = _remove_track_binding(
                person.active_track_bindings,
                camera_uuid,
                int(track_id),
            )
            person.current_cameras = _current_cameras_from_bindings(person.active_track_bindings)
            person.last_seen_at = observed_at
            person.is_active = bool(person.active_track_bindings)
        await db.flush()
        return CanonicalTrackClosure(session_person_id=binding.session_person_id, closed=True)

    async def _resolve_or_create_person(
        self,
        db,
        *,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        global_id: int,
        observed_at: datetime,
    ) -> tuple[SessionPerson, bool]:
        cached = await self.redis.hget(session_person_map_key(session_id), str(global_id))
        if cached:
            person = await self._load_person(db, uuid.UUID(str(cached)))
            if person is not None:
                return person, False

        person = SessionPerson(
            id=uuid.uuid4(),
            session_id=session_id,
            tenant_id=self.tenant_id,
            recognition_state="unknown",
            display_name="Unknown",
            current_cameras=[str(camera_id)],
            active_track_bindings=[],
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            is_active=True,
            evidence_summary={
                "source": "ultimate",
                "ultimate_global_id": global_id,
            },
        )
        person.display_name = _unknown_display_name(person.id)
        db.add(person)
        await self.redis.hset(session_person_map_key(session_id), str(global_id), str(person.id))
        return person, True

    async def _load_person(self, db, person_id: uuid.UUID) -> SessionPerson | None:
        return await db.get(SessionPerson, person_id)

    async def _load_active_binding(
        self,
        db,
        *,
        session_id: uuid.UUID,
        camera_id: uuid.UUID,
        track_id: int,
    ) -> SessionPersonTrackBinding | None:
        result = await db.execute(
            select(SessionPersonTrackBinding).where(
                SessionPersonTrackBinding.session_id == session_id,
                SessionPersonTrackBinding.camera_id == camera_id,
                SessionPersonTrackBinding.track_id == int(track_id),
                SessionPersonTrackBinding.binding_state == "active",
            )
        )
        return result.scalar_one_or_none()
