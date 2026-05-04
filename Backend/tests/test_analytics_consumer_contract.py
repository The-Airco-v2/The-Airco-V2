from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from analytics_consumer import main as analytics_main


@pytest.mark.asyncio
async def test_handle_track_event_skips_when_no_canonical_person_exists(monkeypatch):
    async def fake_find_existing_person_id(*args, **kwargs):
        return None

    monkeypatch.setattr(analytics_main, "_find_existing_person_id", fake_find_existing_person_id)

    db = SimpleNamespace(add=Mock())
    dwell = SimpleNamespace(track_entered=Mock(), track_exited=Mock(return_value=None))
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID] = {}
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    ts = datetime(2026, 4, 4, 2, 0, tzinfo=timezone.utc)

    person_id = await analytics_main._handle_track_event(
        db,
        dwell=dwell,
        track_to_person=track_to_person,
        session_id=session_id,
        camera_id=camera_id,
        track_id=11,
        event_type="track_started",
        ts=ts,
    )

    assert person_id is None
    assert track_to_person == {}
    db.add.assert_not_called()
    dwell.track_entered.assert_not_called()


@pytest.mark.asyncio
async def test_handle_track_event_reuses_identity_owned_person(monkeypatch):
    person_id = uuid.uuid4()

    async def fake_find_existing_person_id(*args, **kwargs):
        return person_id

    monkeypatch.setattr(analytics_main, "_find_existing_person_id", fake_find_existing_person_id)

    db = SimpleNamespace(add=Mock())
    dwell = SimpleNamespace(track_entered=Mock(), track_exited=Mock(return_value=None))
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID] = {}
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    ts = datetime(2026, 4, 4, 2, 5, tzinfo=timezone.utc)

    resolved = await analytics_main._handle_track_event(
        db,
        dwell=dwell,
        track_to_person=track_to_person,
        session_id=session_id,
        camera_id=camera_id,
        track_id=11,
        event_type="track_started",
        ts=ts,
    )

    assert resolved == person_id
    assert track_to_person == {
        analytics_main._track_person_key(session_id, camera_id, 11): person_id,
    }
    db.add.assert_not_called()
    dwell.track_entered.assert_called_once_with(person_id, camera_id, ts)
