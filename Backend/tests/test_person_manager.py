from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api_fakes import scalar_all_result, scalar_one_result
from airco.models import IdentityCluster, PersonEmbedding, SessionPerson, SessionPersonTrackBinding
from identity_consumer.person_manager import PersonManager


@pytest.mark.asyncio
async def test_handle_track_started_materializes_unknown_session_person(monkeypatch):
    added: list[object] = []
    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                scalar_one_result(None),
                scalar_all_result([]),
                scalar_all_result([]),
            ]
        ),
    )

    published_events: list[tuple[str, dict]] = []

    async def fake_publish_event(stream: str, event: dict):
        published_events.append((stream, event))
        return "1-0"

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(
        db=db,
        face_matcher=object(),
        merger=object(),
    )

    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    timestamp = datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc)

    person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_id,
        track_id=7,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.92,
        timestamp=timestamp,
    )

    assert person_id
    assert len(added) == 2
    assert isinstance(added[0], SessionPerson)
    assert isinstance(added[1], SessionPersonTrackBinding)

    person = added[0]
    binding = added[1]
    assert person.id == person_id
    assert person.session_id == session_id
    assert person.recognition_state == "unknown"
    assert person.employee_id is None
    assert person.display_name.startswith("Unknown Person ")
    assert person.display_name == f"Unknown Person {person_id.hex[:8].upper()}"
    assert person.current_cameras == [str(camera_id)]
    assert person.is_active is True
    assert person.first_seen_at == timestamp
    assert person.last_seen_at == timestamp
    assert binding.session_id == session_id
    assert binding.session_person_id == person_id
    assert binding.camera_id == camera_id
    assert binding.track_id == 7
    assert binding.binding_state == "active"
    assert binding.started_at == timestamp
    assert binding.last_seen_at == timestamp
    assert binding.ended_at is None
    assert binding.source == "direct_track"
    assert binding.confidence == pytest.approx(0.92)

    db.flush.assert_awaited_once()
    assert published_events == [
        (
            "airco:identity",
            {
                "event_type": "person_created",
                "session_id": str(session_id),
                "timestamp": "2026-04-03T10:30:00Z",
                "session_person_id": str(person_id),
                "recognition_state": "unknown",
                "track_id": "7",
                "employee_id": "None",
                "employee_name": "None",
                "camera_id": str(camera_id),
                "confidence": "0.0",
            },
        )
    ]


@pytest.mark.asyncio
async def test_handle_body_crop_rebinds_track_to_embedding_matched_person():
    added: list[object] = []
    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(),
    )

    manager = PersonManager(
        db=db,
        face_matcher=object(),
        merger=object(),
    )

    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    track_id = 17
    timestamp = datetime(2026, 4, 4, 12, 30, tzinfo=timezone.utc)
    fresh_person_id = uuid.uuid4()
    canonical_person_id = uuid.uuid4()

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=fresh_person_id,
        camera_id=camera_id,
        track_id=track_id,
        binding_state="active",
        started_at=timestamp,
        last_seen_at=timestamp,
        source="direct_track",
    )
    fresh_person = SessionPerson(
        id=fresh_person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown #17",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": track_id}],
        first_seen_at=timestamp,
        last_seen_at=timestamp,
        is_active=True,
        evidence_summary={},
    )
    canonical_person = SessionPerson(
        id=canonical_person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[],
        active_track_bindings=[],
        first_seen_at=timestamp,
        last_seen_at=timestamp,
        is_active=False,
        evidence_summary={},
    )

    manager._load_active_binding = AsyncMock(return_value=binding)
    manager._load_person = AsyncMock(
        side_effect=lambda person_id: fresh_person if person_id == fresh_person_id else canonical_person
    )
    manager._resolve_body_embedding_reassociation = AsyncMock(return_value=canonical_person)
    manager._is_diverse_body_embedding = AsyncMock(return_value=True)
    manager._load_active_bindings_for_person = AsyncMock(return_value=[])
    manager._ensure_state_machine = AsyncMock(return_value=None)

    await manager.handle_crop(
        session_id=session_id,
        camera_id=camera_id,
        track_id=track_id,
        embedding=[0.1, 0.2, 0.3],
        quality_score=0.92,
        crop_type="body_crop",
        timestamp=timestamp,
    )

    assert binding.session_person_id == canonical_person_id
    assert binding.source == "embedding_reassoc"
    assert fresh_person.merged_into_session_person_id == canonical_person_id
    assert fresh_person.is_active is False
    assert fresh_person.current_cameras == []
    assert fresh_person.active_track_bindings == []
    assert canonical_person.is_active is True
    assert canonical_person.current_cameras == [str(camera_id)]
    assert canonical_person.active_track_bindings == [{"camera_id": str(camera_id), "track_id": track_id}]

    embedding_rows = [model for model in added if isinstance(model, PersonEmbedding)]
    assert len(embedding_rows) == 1
    assert embedding_rows[0].session_person_id == canonical_person_id
    assert embedding_rows[0].embedding_type == "body"


@pytest.mark.asyncio
async def test_handle_body_crop_links_person_to_reviewed_anonymous_identity_cluster():
    added: list[object] = []
    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(),
    )

    manager = PersonManager(
        db=db,
        face_matcher=object(),
        merger=object(),
    )

    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    track_id = 21
    timestamp = datetime(2026, 4, 12, 12, 45, tzinfo=timezone.utc)
    person_id = uuid.uuid4()
    cluster_id = uuid.uuid4()

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=track_id,
        binding_state="active",
        started_at=timestamp,
        last_seen_at=timestamp,
        source="direct_track",
    )
    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown Person ABCD1234",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": track_id}],
        first_seen_at=timestamp,
        last_seen_at=timestamp,
        is_active=True,
        evidence_summary={},
    )
    cluster = IdentityCluster(
        id=cluster_id,
        tenant_id="default",
        employee_id=None,
        cluster_state="anonymous",
        display_label="Merged Anonymous Identity",
        body_template=[0.9, 0.1, 0.0],
    )

    manager._load_active_binding = AsyncMock(return_value=binding)
    manager._load_person = AsyncMock(return_value=person)
    manager._resolve_body_embedding_reassociation = AsyncMock(return_value=None)
    manager._resolve_identity_cluster_match = AsyncMock(return_value=(cluster, 0.94))
    manager._is_diverse_body_embedding = AsyncMock(return_value=True)
    manager._ensure_state_machine = AsyncMock(return_value=None)

    await manager.handle_crop(
        session_id=session_id,
        camera_id=camera_id,
        track_id=track_id,
        embedding=[0.88, 0.12, 0.0],
        quality_score=0.91,
        crop_type="body_crop",
        timestamp=timestamp,
    )

    assert person.identity_cluster_id == cluster_id
    assert person.recognition_state == "unknown"
    embedding_rows = [model for model in added if isinstance(model, PersonEmbedding)]
    assert len(embedding_rows) == 1
    assert embedding_rows[0].session_person_id == person_id
