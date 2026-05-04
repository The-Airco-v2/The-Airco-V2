from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api_fakes import scalar_all_result, scalar_one_result
from airco.models import SessionPerson, SessionPersonTrackBinding
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


@pytest.mark.asyncio
async def test_load_active_bindings_recovers_binding_cache_from_db():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    timestamp = datetime(2026, 4, 4, 9, 0, tzinfo=timezone.utc)

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=12,
        binding_state="active",
        started_at=timestamp,
        last_seen_at=timestamp,
    )
    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 12}],
        first_seen_at=timestamp,
        last_seen_at=timestamp,
        is_active=True,
        evidence_summary={},
    )
    db = _make_db(execute_results=[scalar_all_result([binding]), scalar_one_result(person)])

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    await manager.load_active_bindings()

    assert manager._track_bindings[(str(session_id), str(camera_id), 12)] == person_id
    assert person_id in manager._state_machines


@pytest.mark.asyncio
async def test_handle_track_started_reuses_existing_active_binding_without_creating_new_person(monkeypatch):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    timestamp = datetime(2026, 4, 4, 9, 10, tzinfo=timezone.utc)

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=7,
        binding_state="active",
        started_at=timestamp,
        last_seen_at=timestamp,
    )
    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 7}],
        first_seen_at=timestamp,
        last_seen_at=timestamp,
        is_active=True,
        evidence_summary={},
    )
    db = _make_db(execute_results=[scalar_one_result(binding), scalar_one_result(person)])

    async def fake_publish_event(stream: str, event: dict):
        raise AssertionError("track_started should not publish person_created when binding already exists")

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    resolved_person_id = await manager.handle_track_started(
        session_id=session_id,
        camera_id=camera_id,
        track_id=7,
        bbox=[0.1, 0.2, 0.3, 0.4],
        confidence=0.88,
        timestamp=timestamp,
    )

    assert resolved_person_id == person_id
    assert db.added == []


@pytest.mark.asyncio
async def test_handle_track_observed_updates_binding_and_person_last_seen():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    observed_at = datetime(2026, 4, 4, 9, 20, tzinfo=timezone.utc)
    initial_seen_at = datetime(2026, 4, 4, 9, 15, tzinfo=timezone.utc)

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=5,
        binding_state="active",
        started_at=initial_seen_at,
        last_seen_at=initial_seen_at,
    )
    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 5}],
        first_seen_at=initial_seen_at,
        last_seen_at=initial_seen_at,
        is_active=True,
        evidence_summary={},
    )
    db = _make_db(execute_results=[scalar_one_result(binding), scalar_one_result(person)])

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    await manager.handle_track_observed(
        session_id=session_id,
        camera_id=camera_id,
        track_id=5,
        bbox=[0.1, 0.2, 0.3, 0.4],
        timestamp=observed_at,
    )

    assert binding.last_seen_at == observed_at
    assert person.last_seen_at == observed_at


@pytest.mark.asyncio
async def test_handle_track_ended_closes_binding_and_marks_person_inactive():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    started_at = datetime(2026, 4, 4, 9, 25, tzinfo=timezone.utc)
    ended_at = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=8,
        binding_state="active",
        started_at=started_at,
        last_seen_at=started_at,
    )
    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 8}],
        first_seen_at=started_at,
        last_seen_at=started_at,
        is_active=True,
        evidence_summary={},
    )
    db = _make_db(
        execute_results=[
            scalar_one_result(binding),
            scalar_one_result(person),
            scalar_all_result([]),
        ]
    )

    manager = PersonManager(db=db, face_matcher=object(), merger=object())

    await manager.handle_track_ended(
        session_id=session_id,
        camera_id=camera_id,
        track_id=8,
        timestamp=ended_at,
    )

    assert binding.binding_state == "closed"
    assert binding.last_seen_at == ended_at
    assert binding.ended_at == ended_at
    assert person.last_seen_at == ended_at
    assert person.current_cameras == []
    assert person.active_track_bindings == []
    assert person.is_active is False


@pytest.mark.asyncio
async def test_handle_crop_after_binding_recovery_upgrades_same_session_person_in_place(monkeypatch):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    started_at = datetime(2026, 4, 4, 9, 35, tzinfo=timezone.utc)

    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=11,
        binding_state="active",
        started_at=started_at,
        last_seen_at=started_at,
    )
    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 11}],
        first_seen_at=started_at,
        last_seen_at=started_at,
        is_active=True,
        evidence_summary={},
    )
    db = _make_db(
        execute_results=[
            scalar_all_result([binding]),
            scalar_one_result(person),
            scalar_one_result(binding),
            scalar_one_result(person),
            scalar_one_result(binding),
            scalar_one_result(person),
            scalar_one_result(binding),
            scalar_one_result(person),
            scalar_all_result([]),  # _maybe_auto_enroll_template on call 3 (identified)
        ]
    )

    published_events: list[tuple[str, dict]] = []

    async def fake_publish_event(stream: str, event: dict):
        published_events.append((stream, event))
        return "1-0"

    monkeypatch.setattr("identity_consumer.person_manager.publish_event", fake_publish_event)

    manager = PersonManager(
        db=db,
        face_matcher=SimpleNamespace(
            find_top_matches=lambda query, templates, top_k=3, **kwargs: [
                {"employee_id": str(employee_id), "score": 0.91, "band": "accept"}
            ]
        ),
        merger=object(),
    )
    monkeypatch.setattr(
        manager,
        "_load_employee_templates",
        AsyncMock(return_value={str(employee_id): [[0.1, 0.2, 0.3]]}),
    )
    monkeypatch.setattr(manager, "_get_employee_name", AsyncMock(return_value="Alice"))
    monkeypatch.setattr(manager, "_get_camera_thresholds", AsyncMock(return_value={}))

    await manager.load_active_bindings()

    for offset in range(3):
        await manager.handle_crop(
            session_id=session_id,
            camera_id=camera_id,
            track_id=11,
            embedding=[0.1, 0.2, 0.3],
            quality_score=0.95,
            crop_type="face_crop",
            timestamp=started_at.replace(minute=35 + offset),
        )

    assert person.id == person_id
    assert person.employee_id == employee_id
    assert person.recognition_state == "identified"
    assert person.display_name == "Alice"
    assert [event["event_type"] for _, event in published_events] == [
        "person_candidate",
        "person_identified",
    ]
