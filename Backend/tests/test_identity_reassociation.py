from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api_fakes import scalar_all_result, scalar_one_result
from airco.models import PersonEmbedding, SessionPerson, SessionPersonTrackBinding
from identity_consumer.cross_camera import CameraTopology, CrossCameraMerger
from identity_consumer.person_manager import PersonManager


def _make_db(*, execute_results: list[object]):
    added: list[object] = []

    db = SimpleNamespace(
        add=lambda model: added.append(model),
        flush=AsyncMock(),
        execute=AsyncMock(side_effect=execute_results),
    )
    db.added = added
    return db


def _make_unknown_person(
    *,
    person_id: uuid.UUID,
    session_id: uuid.UUID,
    camera_id: uuid.UUID,
    seen_at: datetime,
    face_confidence: float | None = None,
    is_active: bool = False,
) -> SessionPerson:
    return SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)] if is_active else [],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 11}] if is_active else [],
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        is_active=is_active,
        face_confidence=face_confidence,
        evidence_summary={},
    )


@pytest.mark.asyncio
async def test_exact_active_binding_wins_before_reassociation(monkeypatch):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    timestamp = datetime(2026, 4, 4, 11, 0, tzinfo=timezone.utc)

    active_binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=4,
        binding_state="active",
        started_at=timestamp - timedelta(seconds=4),
        last_seen_at=timestamp - timedelta(seconds=1),
        source="direct_track",
    )
    person = _make_unknown_person(
        person_id=person_id,
        session_id=session_id,
        camera_id=camera_id,
        seen_at=timestamp - timedelta(seconds=1),
        is_active=True,
    )
    db = _make_db(execute_results=[scalar_one_result(active_binding), scalar_one_result(person)])

    async def fake_publish_event(stream: str, event: dict):
        raise AssertionError("existing active binding should not create a new person")

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    resolved_person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_id,
        track_id=4,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.9,
        timestamp=timestamp,
    )

    assert resolved_person_id == person_id
    assert db.added == []


@pytest.mark.asyncio
async def test_recent_same_camera_reassociation_reuses_canonical_person(monkeypatch):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    closed_at = datetime(2026, 4, 4, 11, 5, tzinfo=timezone.utc)
    started_at = closed_at + timedelta(seconds=6)

    recent_binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=11,
        binding_state="closed",
        started_at=closed_at - timedelta(seconds=25),
        last_seen_at=closed_at,
        ended_at=closed_at,
        source="direct_track",
    )
    person = _make_unknown_person(
        person_id=person_id,
        session_id=session_id,
        camera_id=camera_id,
        seen_at=closed_at,
    )
    db = _make_db(
        execute_results=[
            scalar_one_result(None),
            scalar_all_result([recent_binding]),
            scalar_one_result(person),
            scalar_all_result([]),
        ]
    )

    async def fake_publish_event(stream: str, event: dict):
        raise AssertionError("same-camera reassociation should not publish person_created")

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    resolved_person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_id,
        track_id=12,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.86,
        timestamp=started_at,
    )

    assert resolved_person_id == person_id
    assert len(db.added) == 1
    binding = db.added[0]
    assert isinstance(binding, SessionPersonTrackBinding)
    assert binding.session_person_id == person_id
    assert binding.track_id == 12
    assert binding.source == "same_camera_reassoc"
    assert person.last_seen_at == started_at
    assert person.is_active is True
    assert person.current_cameras == [str(camera_id)]
    assert person.active_track_bindings == [{"camera_id": str(camera_id), "track_id": 12}]


@pytest.mark.asyncio
async def test_recent_same_camera_reassociation_allows_same_track_id_after_noisy_end(monkeypatch):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    closed_at = datetime(2026, 4, 4, 11, 7, tzinfo=timezone.utc)
    restarted_at = closed_at + timedelta(seconds=2)

    recent_binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=11,
        binding_state="closed",
        started_at=closed_at - timedelta(seconds=20),
        last_seen_at=closed_at,
        ended_at=closed_at,
        source="direct_track",
    )
    person = _make_unknown_person(
        person_id=person_id,
        session_id=session_id,
        camera_id=camera_id,
        seen_at=closed_at,
    )
    db = _make_db(
        execute_results=[
            scalar_one_result(None),
            scalar_all_result([recent_binding]),
            scalar_one_result(person),
            scalar_all_result([]),
        ]
    )

    async def fake_publish_event(stream: str, event: dict):
        raise AssertionError("same-track same-camera reassociation should not publish person_created")

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    resolved_person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_id,
        track_id=11,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.84,
        timestamp=restarted_at,
    )

    assert resolved_person_id == person_id
    assert len(db.added) == 1
    binding = db.added[0]
    assert isinstance(binding, SessionPersonTrackBinding)
    assert binding.session_person_id == person_id
    assert binding.track_id == 11
    assert binding.source == "same_camera_reassoc"
    assert person.last_seen_at == restarted_at
    assert person.is_active is True
    assert person.current_cameras == [str(camera_id)]
    assert person.active_track_bindings == [{"camera_id": str(camera_id), "track_id": 11}]


@pytest.mark.asyncio
async def test_cross_camera_reassociation_requires_strong_evidence(monkeypatch):
    session_id = uuid.uuid4()
    camera_a = uuid.uuid4()
    camera_b = uuid.uuid4()
    person_id = uuid.uuid4()
    closed_at = datetime(2026, 4, 4, 11, 20, tzinfo=timezone.utc)
    started_at = closed_at + timedelta(seconds=12)

    recent_binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_a,
        track_id=31,
        binding_state="closed",
        started_at=closed_at - timedelta(seconds=20),
        last_seen_at=closed_at,
        ended_at=closed_at,
        source="direct_track",
    )
    person = _make_unknown_person(
        person_id=person_id,
        session_id=session_id,
        camera_id=camera_a,
        seen_at=closed_at,
        face_confidence=0.96,
    )

    topology = CameraTopology()
    topology.add_pair(str(camera_a), str(camera_b), overlap_type="adjacent", transition_min=5, transition_max=60)
    merger = CrossCameraMerger(topology)

    db = _make_db(
        execute_results=[
            scalar_one_result(None),
            scalar_all_result([]),
            scalar_all_result([recent_binding]),
            scalar_one_result(person),
            scalar_all_result([]),
        ]
    )

    async def fake_publish_event(stream: str, event: dict):
        raise AssertionError("cross-camera reassociation should not publish person_created")

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(db=db, face_matcher=object(), merger=merger)

    resolved_person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_b,
        track_id=32,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.97,
        timestamp=started_at,
    )

    assert resolved_person_id == person_id
    assert len(db.added) == 1
    binding = db.added[0]
    assert isinstance(binding, SessionPersonTrackBinding)
    assert binding.session_person_id == person_id
    assert binding.camera_id == camera_b
    assert binding.track_id == 32
    assert binding.source == "cross_camera_reassoc"
    assert person.last_seen_at == started_at
    assert person.current_cameras == [str(camera_b)]
    assert person.active_track_bindings == [{"camera_id": str(camera_b), "track_id": 32}]


@pytest.mark.asyncio
async def test_body_crop_reassociates_using_live_osnet_gallery_match():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    track_id = 41
    timestamp = datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc)
    fresh_person_id = uuid.uuid4()
    canonical_person_id = uuid.uuid4()

    active_binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=fresh_person_id,
        camera_id=camera_id,
        track_id=track_id,
        binding_state="active",
        started_at=timestamp - timedelta(seconds=3),
        last_seen_at=timestamp - timedelta(seconds=1),
        source="direct_track",
    )
    fresh_person = _make_unknown_person(
        person_id=fresh_person_id,
        session_id=session_id,
        camera_id=camera_id,
        seen_at=timestamp - timedelta(seconds=1),
        is_active=True,
    )
    canonical_person = _make_unknown_person(
        person_id=canonical_person_id,
        session_id=session_id,
        camera_id=camera_id,
        seen_at=timestamp - timedelta(seconds=4),
    )
    gallery_embedding = SimpleNamespace(
        session_person_id=canonical_person_id,
        embedding=[0.99, 0.08, 0.0],
    )
    db = _make_db(
        execute_results=[
            scalar_one_result(active_binding),
            scalar_one_result(fresh_person),
            scalar_all_result([canonical_person_id]),
            scalar_all_result([gallery_embedding]),
            scalar_one_result(canonical_person),
            scalar_all_result([]),
            scalar_all_result([]),
            scalar_all_result([]),
        ]
    )

    manager = PersonManager(
        db=db,
        face_matcher=object(),
        merger=CrossCameraMerger(CameraTopology()),
    )

    await manager.handle_crop(
        session_id=session_id,
        camera_id=camera_id,
        track_id=track_id,
        embedding=[0.98, 0.1, 0.0],
        quality_score=0.91,
        crop_type="body_crop",
        timestamp=timestamp,
    )

    assert active_binding.session_person_id == canonical_person_id
    assert active_binding.source == "embedding_reassoc"
    assert fresh_person.merged_into_session_person_id == canonical_person_id
    assert fresh_person.is_active is False
    assert fresh_person.current_cameras == []
    assert fresh_person.active_track_bindings == []
    assert canonical_person.is_active is True
    assert canonical_person.current_cameras == [str(camera_id)]
    assert canonical_person.active_track_bindings == [{"camera_id": str(camera_id), "track_id": track_id}]

    embedding_rows = [model for model in db.added if isinstance(model, PersonEmbedding)]
    assert len(embedding_rows) == 1
    assert embedding_rows[0].session_person_id == canonical_person_id
    assert embedding_rows[0].embedding_type == "body"


@pytest.mark.asyncio
async def test_ambiguous_or_weak_reassociation_stays_conservative(monkeypatch):
    session_id = uuid.uuid4()
    camera_a = uuid.uuid4()
    camera_b = uuid.uuid4()
    started_at = datetime(2026, 4, 4, 11, 35, tzinfo=timezone.utc)

    weak_person = _make_unknown_person(
        person_id=uuid.uuid4(),
        session_id=session_id,
        camera_id=camera_a,
        seen_at=started_at - timedelta(seconds=8),
        face_confidence=0.15,
    )
    weak_binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=weak_person.id,
        camera_id=camera_a,
        track_id=50,
        binding_state="closed",
        started_at=started_at - timedelta(seconds=20),
        last_seen_at=started_at - timedelta(seconds=8),
        ended_at=started_at - timedelta(seconds=8),
        source="direct_track",
    )

    topology = CameraTopology()
    topology.add_pair(str(camera_a), str(camera_b), overlap_type="adjacent", transition_min=5, transition_max=60)
    merger = CrossCameraMerger(topology)

    db = _make_db(
        execute_results=[
            scalar_one_result(None),
            scalar_all_result([]),
            scalar_all_result([weak_binding]),
            scalar_one_result(weak_person),
        ]
    )

    published_events: list[tuple[str, dict]] = []

    async def fake_publish_event(stream: str, event: dict):
        published_events.append((stream, event))
        return "1-0"

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(db=db, face_matcher=object(), merger=merger)

    resolved_person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_b,
        track_id=51,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.58,
        timestamp=started_at,
    )

    session_people = [model for model in db.added if isinstance(model, SessionPerson)]
    bindings = [model for model in db.added if isinstance(model, SessionPersonTrackBinding)]

    assert resolved_person_id == session_people[0].id
    assert len(session_people) == 1
    assert len(bindings) == 1
    assert bindings[0].source == "direct_track"
    assert published_events[0][1]["event_type"] == "person_created"
