"""Tests for co-occurrence contradiction checks."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import pytest
from airco.models import SessionPerson, SessionPersonTrackBinding
from identity_consumer.person_manager import PersonManager

def _make_binding(*, session_id, person_id, camera_id, track_id, started_at, ended_at=None, state="active"):
    return SessionPersonTrackBinding(
        id=uuid.uuid4(), session_id=session_id, session_person_id=person_id,
        camera_id=camera_id, track_id=track_id, binding_state=state,
        started_at=started_at, last_seen_at=ended_at or started_at,
        ended_at=ended_at, source="direct_track",
    )

@pytest.mark.asyncio
async def test_co_occurrence_blocks_when_same_camera_overlapping():
    """Two persons tracked on same camera at same time = contradiction."""
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_a = uuid.uuid4()
    person_b = uuid.uuid4()
    t = datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc)
    binding_a = _make_binding(session_id=session_id, person_id=person_a, camera_id=camera_id, track_id=1, started_at=t, ended_at=t + timedelta(seconds=30), state="closed")
    binding_b = _make_binding(session_id=session_id, person_id=person_b, camera_id=camera_id, track_id=2, started_at=t + timedelta(seconds=10), ended_at=t + timedelta(seconds=40), state="closed")
    db = SimpleNamespace(add=lambda model: None, flush=AsyncMock(), execute=AsyncMock())
    manager = PersonManager(db=db, face_matcher=object(), merger=object())
    manager._load_bindings_for_persons = AsyncMock(return_value=[binding_a, binding_b])
    result = await manager._has_co_occurrence_contradiction(person_a, person_b)
    assert result is True

@pytest.mark.asyncio
async def test_no_co_occurrence_different_cameras():
    """Persons on different cameras at same time = fine."""
    session_id = uuid.uuid4()
    cam1 = uuid.uuid4()
    cam2 = uuid.uuid4()
    person_a = uuid.uuid4()
    person_b = uuid.uuid4()
    t = datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc)
    binding_a = _make_binding(session_id=session_id, person_id=person_a, camera_id=cam1, track_id=1, started_at=t, ended_at=t + timedelta(seconds=30), state="closed")
    binding_b = _make_binding(session_id=session_id, person_id=person_b, camera_id=cam2, track_id=2, started_at=t + timedelta(seconds=10), ended_at=t + timedelta(seconds=40), state="closed")
    db = SimpleNamespace(add=lambda model: None, flush=AsyncMock(), execute=AsyncMock())
    manager = PersonManager(db=db, face_matcher=object(), merger=object())
    manager._load_bindings_for_persons = AsyncMock(return_value=[binding_a, binding_b])
    result = await manager._has_co_occurrence_contradiction(person_a, person_b)
    assert result is False

@pytest.mark.asyncio
async def test_no_co_occurrence_sequential_same_camera():
    """Persons on same camera but different times = fine."""
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_a = uuid.uuid4()
    person_b = uuid.uuid4()
    t = datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc)
    binding_a = _make_binding(session_id=session_id, person_id=person_a, camera_id=camera_id, track_id=1, started_at=t, ended_at=t + timedelta(seconds=30), state="closed")
    binding_b = _make_binding(session_id=session_id, person_id=person_b, camera_id=camera_id, track_id=2, started_at=t + timedelta(seconds=35), ended_at=t + timedelta(seconds=60), state="closed")
    db = SimpleNamespace(add=lambda model: None, flush=AsyncMock(), execute=AsyncMock())
    manager = PersonManager(db=db, face_matcher=object(), merger=object())
    manager._load_bindings_for_persons = AsyncMock(return_value=[binding_a, binding_b])
    result = await manager._has_co_occurrence_contradiction(person_a, person_b)
    assert result is False
