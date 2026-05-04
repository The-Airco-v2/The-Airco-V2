from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api_fakes import scalar_one_result
from airco.models import SessionPerson
from snapshot_consumer.main import _resolve_session_person_id, _update_best_thumbnail


def _make_db(*, execute_results: list[object] | None = None, person: SessionPerson | None = None):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=execute_results or []),
        get=AsyncMock(return_value=person),
    )


@pytest.mark.asyncio
async def test_resolve_session_person_id_prefers_event_session_person_id():
    person_id = uuid.uuid4()
    db = _make_db()

    resolved = await _resolve_session_person_id(
        db,
        session_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        track_id=14,
        event_session_person_id=str(person_id),
    )

    assert resolved == person_id
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_session_person_id_falls_back_to_track_binding_with_retry(monkeypatch):
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    db = _make_db(execute_results=[scalar_one_result(None), scalar_one_result(person_id)])
    sleep_mock = AsyncMock()
    monkeypatch.setattr("snapshot_consumer.main.asyncio.sleep", sleep_mock)

    resolved = await _resolve_session_person_id(
        db,
        session_id=session_id,
        camera_id=camera_id,
        track_id=982,
        event_session_person_id=None,
        max_attempts=2,
        retry_delay_seconds=0.01,
    )

    assert resolved == person_id
    assert db.execute.await_count == 2
    sleep_mock.assert_awaited_once_with(0.01)


@pytest.mark.asyncio
async def test_resolve_session_person_id_uses_recent_closed_binding_when_active_binding_is_missing():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    db = _make_db(execute_results=[scalar_one_result(None), scalar_one_result(person_id)])

    resolved = await _resolve_session_person_id(
        db,
        session_id=session_id,
        camera_id=camera_id,
        track_id=244,
        event_session_person_id=None,
        max_attempts=1,
        recent_binding_window_seconds=120,
    )

    assert resolved == person_id
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_update_best_thumbnail_sets_first_snapshot_and_only_upgrades_for_better_score():
    person_id = uuid.uuid4()
    first_seen = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    person = SessionPerson(
        id=person_id,
        session_id=uuid.uuid4(),
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[],
        active_track_bindings=[],
        first_seen_at=first_seen,
        last_seen_at=first_seen,
        is_active=True,
        best_thumbnail_url=None,
        evidence_summary={},
    )
    db = _make_db(person=person)

    await _update_best_thumbnail(
        db,
        session_person_id=person_id,
        full_frame_url="minio://snapshots/first.jpg",
        score=0.32,
    )

    assert person.best_thumbnail_url == "minio://snapshots/first.jpg"
    assert person.evidence_summary["best_thumbnail_score"] == 0.32

    await _update_best_thumbnail(
        db,
        session_person_id=person_id,
        full_frame_url="minio://snapshots/lower.jpg",
        score=0.18,
    )

    assert person.best_thumbnail_url == "minio://snapshots/first.jpg"
    assert person.evidence_summary["best_thumbnail_score"] == 0.32

    await _update_best_thumbnail(
        db,
        session_person_id=person_id,
        full_frame_url="minio://snapshots/better.jpg",
        score=0.74,
    )

    assert person.best_thumbnail_url == "minio://snapshots/better.jpg"
    assert person.evidence_summary["best_thumbnail_score"] == 0.74
