from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from api_fakes import scalar_one_result
from airco.models import CameraPresenceSegment
from analytics_consumer import main as analytics_main


def _make_db(*execute_results):
    return SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        execute=AsyncMock(side_effect=list(execute_results)),
    )


@pytest.mark.asyncio
async def test_handle_track_event_resolves_from_canonical_binding_row():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    ts = datetime(2026, 4, 4, 2, 0, tzinfo=timezone.utc)

    db = _make_db(scalar_one_result(person_id))
    dwell = SimpleNamespace(track_entered=Mock(), track_exited=Mock(return_value=None))
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID] = {}

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
    dwell.track_entered.assert_called_once_with(person_id, camera_id, ts)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_handle_track_event_does_not_fall_back_to_session_person_active_bindings():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    ts = datetime(2026, 4, 4, 2, 5, tzinfo=timezone.utc)

    db = _make_db(
        scalar_one_result(None),
        scalar_one_result(person_id),
    )
    dwell = SimpleNamespace(track_entered=Mock(), track_exited=Mock(return_value=None))
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID] = {}

    resolved = await analytics_main._handle_track_event(
        db,
        dwell=dwell,
        track_to_person=track_to_person,
        session_id=session_id,
        camera_id=camera_id,
        track_id=12,
        event_type="track_started",
        ts=ts,
    )

    assert resolved is None
    assert track_to_person == {}
    dwell.track_entered.assert_not_called()
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_handle_track_event_materializes_presence_segment_from_canonical_binding():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    ts_started = datetime(2026, 4, 4, 2, 10, tzinfo=timezone.utc)
    ts_ended = datetime(2026, 4, 4, 2, 15, tzinfo=timezone.utc)

    db = _make_db(scalar_one_result(person_id), scalar_one_result(person_id))
    dwell = SimpleNamespace(
        track_entered=Mock(),
        track_exited=Mock(
            return_value={
                "entered_at": ts_started,
                "exited_at": ts_ended,
                "dwell_seconds": 300.0,
            }
        ),
    )
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID] = {}

    started = await analytics_main._handle_track_event(
        db,
        dwell=dwell,
        track_to_person=track_to_person,
        session_id=session_id,
        camera_id=camera_id,
        track_id=13,
        event_type="track_started",
        ts=ts_started,
    )
    ended = await analytics_main._handle_track_event(
        db,
        dwell=dwell,
        track_to_person=track_to_person,
        session_id=session_id,
        camera_id=camera_id,
        track_id=13,
        event_type="track_ended",
        ts=ts_ended,
    )

    assert started == person_id
    assert ended == person_id
    assert track_to_person == {}
    assert db.add.call_count == 1
    assert isinstance(db.add.call_args.args[0], CameraPresenceSegment)
    segment = db.add.call_args.args[0]
    assert segment.session_person_id == person_id
    assert segment.camera_id == camera_id
    assert segment.dwell_seconds == 300.0
